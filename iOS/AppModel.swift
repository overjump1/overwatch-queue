import Foundation
import Observation
import SwiftUI
import UserNotifications

/// Wires the store to the PC and to the platform features that react to phase changes:
/// the Live Activity, haptics, sound, and the relay down to the watch.
@MainActor
@Observable
public final class AppModel {
    public let store = QueueStore()
    public let catalog = CatalogService.shared

    /// Which PC this phone is paired with. Nil until a code is scanned — there is no
    /// other source of state, so an unpaired app shows the pairing screen and nothing else.
    public private(set) var pairing: Pairing?

    /// Why the last code was refused, if it was. Shown on the pairing screen — a code
    /// that arrives from the system camera has no other way to report that it wasn't ours.
    public private(set) var pairingProblem: String?

    /// Bumped every time a match lands. Views observe it to fire one-shot animations
    /// without having to diff the phase themselves.
    public private(set) var matchFoundToken = 0

    public var isPaired: Bool { pairing != nil }

    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid

    public init() {
        pairing = Pairing.load()

        store.onPhaseChange = { [weak self] previous, next in
            self?.react(from: previous, to: next)
        }
        store.onSnapshot = { [weak self] snapshot in
            guard let self else { return }
            // The watch mirrors whatever the phone is showing.
            WatchLink.shared.send(snapshot: snapshot)
            // The activity gets the store's drift correction too, so its timers and its
            // wait bar run from the same origin as the ones on screen.
            LiveActivityController.shared.sync(to: snapshot, clock: store.clock)
            SharedState.write(snapshot)
        }
        LiveActivityController.shared.onActivityPushToken = { [weak self] sessionID, token in
            self?.store.registerActivityPushToken(sessionID: sessionID, token: token, environment: .current)
        }
        LiveActivityController.shared.onPushToStartToken = { [weak self] token in
            self?.store.registerActivityStartToken(token, environment: .current)
        }
        // Whatever the activity has to say about itself goes to the server window, where
        // it can be read without the phone in hand.
        LiveActivityController.shared.onDiagnostic = { [weak self] message in
            self?.store.report(message)
        }
    }

    public func start() {
        // Before anything can push: take over the activity a previous launch left on the
        // Lock Screen, rather than stacking a new one on top of it. Covers a push-to-start
        // activity too — the OS may have created one while nothing local was running.
        LiveActivityController.shared.adoptRunningActivity()
        LiveActivityController.shared.observePushToStartToken()
        WatchLink.shared.activate()
        WatchLink.shared.onCommand = { [weak self] command in
            // A vote or hero pick made on the wrist is handled exactly as if it had been
            // tapped on the phone, so both surfaces stay in agreement.
            self?.store.transport?.send(command)
            if case .voteMap(let key) = command { self?.store.vote(map: key) }
            if case .selectHero(let key) = command { self?.store.select(hero: key) }
        }
        // The watch is configured by the same scan the phone was: hand it over on every
        // launch, so one that was off, flat or freshly installed catches up.
        WatchLink.shared.send(pairing: pairing)
        connect()
        Task { await catalog.load() }
    }

    // MARK: - Pairing

    @discardableResult
    public func pair(with code: String) -> Bool {
        guard let scanned = Pairing(pairingCode: code) else {
            pairingProblem = "That isn't a pairing code from the queue server."
            return false
        }
        pairingProblem = nil
        pair(scanned)
        return true
    }

    public func clearPairingProblem() {
        pairingProblem = nil
    }

    public func pair(_ pairing: Pairing) {
        self.pairing = pairing
        pairing.save()
        WatchLink.shared.send(pairing: pairing)
        connect()
    }

    /// Drops the pairing and everything it was showing. The PC keeps its token, so
    /// re-scanning the same code pairs again.
    public func unpair() {
        store.disconnect()
        store.reset()
        Pairing.forget()
        pairing = nil
        WatchLink.shared.send(pairing: nil)
        LiveActivityController.shared.end()
    }

    public func connect() {
        guard let pairing else {
            store.disconnect()
            return
        }
        let identity = ClientIdentity(kind: .phone,
                                      name: UIDevice.current.name,
                                      appVersion: Bundle.main.appVersion)
        store.use(WebSocketTransport(pairing: pairing, identity: identity))
    }

    // MARK: - Push notifications
    //
    // The socket above is the golden path; this is what reaches the phone once iOS has
    // suspended it. Registration doesn't wait on permission — a token unlocks the silent
    // wake-up push regardless, and only a visible alert actually needs the user's okay.

    public func registerForPushNotifications() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .timeSensitive]) { _, _ in }
        UIApplication.shared.registerForRemoteNotifications()
    }

    public func didReceive(deviceToken: Data) {
        store.registerPushToken(deviceToken.hexEncoded, environment: .current)
    }

    /// A background push arrived. The system gives a background launch only a short window
    /// before suspending it again, so the sleep below holds it open long enough for the
    /// snapshot to actually come back.
    public func handleBackgroundPush(completion: @escaping () -> Void) {
        catchUp()
        Task {
            try? await Task.sleep(for: .seconds(3))
            completion()
        }
    }

    /// The player tapped their way in — a notification, or the Live Activity itself (on
    /// the phone or mirrored on the watch).
    ///
    /// This is also the fallback that makes a failed push-to-start recoverable. Starting a
    /// Live Activity from a push is best-effort and, in practice, often simply never
    /// arrives; starting one from inside a running app never fails. So there is no
    /// "start the activity" call here: `catchUp` asks for a fresh snapshot, `onSnapshot`
    /// hands it to `LiveActivityController.sync`, and sync starts the activity because
    /// there isn't one. The refresh *is* the start.
    public func handleUserOpened() {
        // The counterpart of the watch's own line — together they say which device a tap
        // actually landed on, which is the whole question when a card on the wrist opens
        // the phone instead.
        store.report("phone opened by a tap (notification or Live Activity)")
        // Reaching for the app is the player asking to see it, which outvotes having
        // swiped the card away earlier in the same queue.
        LiveActivityController.shared.forgetDismissal()
        catchUp()
    }

    /// Make sure the socket is live and ask for the truth, rather than trusting whatever
    /// arrived to carry it.
    ///
    /// Also re-checks for a Live Activity this process doesn't know about yet — a
    /// push-to-start push can create one entirely OS-side while nothing local was
    /// running, and this is the first chance any app code gets to notice, attach to it,
    /// and register its per-activity push token for every update after this one.
    private func catchUp() {
        LiveActivityController.shared.adoptRunningActivity()
        if store.transport?.status.isLive != true { connect() }
        store.requestRefresh()
    }

    // MARK: - Foregrounding / backgrounding
    //
    // iOS suspends the socket within seconds of the screen locking, and nothing here can
    // change that without APNs — this is cosmetic, not a fix for "phone locked for a
    // while". That's what the watch's own direct-to-PC connection (`WatchTransport`) is
    // for. All this does is avoid an abrupt mid-frame kill on the way out, and skip the
    // transport's own backoff delay on the way back in.

    public func didEnterBackground() {
        guard backgroundTask == .invalid else { return }
        backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "queue-socket-drain") { [weak self] in
            self?.endBackgroundTask()
        }
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(3))
            guard let self, self.backgroundTask != .invalid else { return }
            self.store.transport?.disconnect()
            self.endBackgroundTask()
        }
    }

    public func didBecomeActive() {
        endBackgroundTask()
        if store.transport?.status.isLive != true { connect() }
    }

    private func endBackgroundTask() {
        guard backgroundTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(backgroundTask)
        backgroundTask = .invalid
    }

    // MARK: - Reactions

    /// Pulls the art for a phase into the cache the moment that phase starts, so tiles
    /// are already decoded by the time they animate in — the vote and hero-select windows
    /// are short, and a grid that pops in one portrait at a time wastes them.
    private func prefetchArt(for phase: QueuePhase) {
        switch phase {
        case .mapVote(let info):
            ImageCache.shared.prefetch(info.options.map { catalog.map($0.mapKey)?.screenshot })
        case .heroSelect:
            ImageCache.shared.prefetch(store.selectableHeroes().map(\.portrait))
        default:
            break
        }
    }

    private func react(from previous: QueuePhase, to next: QueuePhase) {
        switch next.kind {
        case .matchFound:
            matchFoundToken += 1
            Haptics.matchFound()
            SoundPlayer.shared.playMatchFound()
        case .mapVote, .heroSelect:
            Haptics.attention()
            prefetchArt(for: next)
        case .idle, .cancelled:
            LiveActivityController.shared.end()
        default:
            break
        }
    }
}
