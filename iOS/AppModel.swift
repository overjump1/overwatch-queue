import Foundation
import Observation
import SwiftUI

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
    }

    public func start() {
        // Before anything can push: take over the activity a previous launch left on the
        // Lock Screen, rather than stacking a new one on top of it.
        LiveActivityController.shared.adoptRunningActivity()
        WatchLink.shared.activate()
        WatchLink.shared.onCommand = { [weak self] command in
            // A vote or hero pick made on the wrist is handled exactly as if it had been
            // tapped on the phone, so both surfaces stay in agreement.
            self?.store.transport?.send(command)
            if case .voteMap(let key) = command { self?.store.vote(map: key) }
            if case .selectHero(let key) = command { self?.store.select(hero: key) }
        }
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
        connect()
    }

    /// Drops the pairing and everything it was showing. The PC keeps its token, so
    /// re-scanning the same code pairs again.
    public func unpair() {
        store.disconnect()
        store.reset()
        Pairing.forget()
        pairing = nil
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
