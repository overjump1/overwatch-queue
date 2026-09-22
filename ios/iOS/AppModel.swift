import ActivityKit
import AVFoundation
import CoreHaptics
import SwiftUI
import UIKit
import WatchConnectivity

@MainActor
final class AppModel: ObservableObject {
    static let shared = AppModel()

    @Published private(set) var pairID: String? = Pairing.id
    @Published private(set) var status: QueueStatus = .idle
    @Published private(set) var reachable = true
    @Published private(set) var pairingWasReset = false
    @Published private(set) var matchAlerts = 0
    /// A newer release, if there is one. iOS can't install it, so this only reports it.
    @Published private(set) var updateNotice: UpdateNotice = .quiet

    private var fcmToken: String?
    private var startToken: String?
    private var updateToken: String?
    /// The activity `updateToken` belongs to, so the token is dropped once that activity ends.
    private var updateTokenActivityID: String?
    private var active = false
    private var launched = false
    private var syncTask: Task<Void, Never>?
    /// Whether the last register's reply carried the state. A worker too old to send one back
    /// must not read as "already refreshed", or the screen would open empty.
    private var registerCarriedStatus = false
    private var observedActivities = Set<String>()
    /// The match we already alerted for, so the same match never alerts twice.
    private var alertedFoundAt: Double?
    private var updateCheckedAt: Date?
    private var updateTask: Task<Void, Never>?
    private let watch = WatchBridge()
    private let alerts = MatchAlert()

    private init() {}

    func launch() {
        guard !launched else { return }
        launched = true
        watch.activate(pairID: pairID)
        Task {
            for await data in Activity<QueueActivityAttributes>.pushToStartTokenUpdates {
                startToken = data.hex
                await register()
            }
        }
        Task {
            for await activity in Activity<QueueActivityAttributes>.activityUpdates {
                observe(activity)
            }
        }
        // The list also holds activities that have ended and are still on the lock screen for their
        // linger, in no documented order, so take the running one rather than whatever is last.
        if let existing = runningActivity ?? Activity<QueueActivityAttributes>.activities.last {
            observe(existing)
        }
    }

    /// A silent push from the worker woke the app: report the current tokens. The register's reply
    /// carries the state back with it, so this costs one request, not two.
    func refreshTokens() async {
        launch()
        await register()
    }

    func setActive(_ isActive: Bool) {
        active = isActive
        syncTask?.cancel()
        syncTask = nil
        guard isActive else { return }
        launch()
        // Checks at most every six hours; coming back to the app just gives it the chance.
        checkForUpdate(force: false)
        // One request on the way in, and no poll after it. The register's reply carries the state,
        // and from then on the Live Activity's pushes carry every change -- coming back to the app
        // is what catches one that never arrived.
        syncTask = Task {
            await register()
            // An older worker answers a register with nothing; ask outright rather than open empty.
            if !registerCarriedStatus { await refresh() }
        }
    }

    func setFCMToken(_ token: String) {
        guard token != fcmToken else { return }
        fcmToken = token
        Task { await register() }
    }

    func pair(with text: String) {
        guard let id = Pairing.parse(text) else { return }
        Pairing.id = id
        pairID = id
        pairingWasReset = false
        status = .idle
        watch.send(pairID: id)
        Task {
            await register()
            await refresh()
        }
    }

    func unpair() {
        Pairing.id = nil
        pairID = nil
        status = .idle
        watch.send(pairID: nil)
        endAllActivities()
    }

    // MARK: - Updates

    /// `force` is the menu item; otherwise this only looks again once six hours have passed. An
    /// update speaks up either way; everything else is only worth saying when the user asked, and
    /// is taken back down once they've read it.
    func checkForUpdate(force: Bool) {
        guard Updates.checksForUpdates, updateTask == nil else { return }
        if !force, let last = updateCheckedAt, Date.now.timeIntervalSince(last) < 6 * 60 * 60 { return }
        if force { updateNotice = .checking }
        updateTask = Task {
            let notice = await Updates.check()
            updateTask = nil
            // A check that couldn't reach GitHub doesn't count, so the next one isn't six hours away.
            if notice != .failed { updateCheckedAt = .now }
            if case .available = notice {
                updateNotice = notice
                return
            }
            updateNotice = force ? notice : .quiet
            guard force else { return }
            try? await Task.sleep(for: .seconds(4))
            if updateNotice == notice { updateNotice = .quiet }
        }
    }

    // MARK: - Worker

    private func refresh() async {
        guard let id = pairID else { return }
        switch await Worker.fetchStatus(pairID: id) {
        case .ok(let fetched):
            reachable = true
            if let fetched, id == pairID { apply(fetched) }
        case .reset:
            handleReset(id)
        case .failed:
            reachable = false
        }
    }

    private func register() async {
        guard let id = pairID else { return }
        // Often called while iOS has only briefly woken the app; don't get suspended mid-request.
        let task = UIApplication.shared.beginBackgroundTask(withName: "register")
        defer { UIApplication.shared.endBackgroundTask(task) }
        if let current = Activity<QueueActivityAttributes>.pushToStartToken {
            startToken = current.hex
        }
        // pushTokenUpdates only runs while the app does, so an activity iOS started from a push
        // while the app was dead has no other way of getting its token to the worker.
        if let running = runningActivity, let token = running.pushToken {
            updateToken = token.hex
            updateTokenActivityID = running.id
        }
        var body: [String: Any] = [
            "kind": "phone",
            "activitiesEnabled": ActivityAuthorizationInfo().areActivitiesEnabled,
            "activityRunning": hasRunningActivity,
        ]
        if let fcmToken { body["fcm"] = fcmToken }
        if let startToken { body["startToken"] = startToken }
        if let updateToken { body["updateToken"] = updateToken }
        switch await Worker.register(pairID: id, body: body) {
        case .reset:
            handleReset(id)
        case .ok(let fetched):
            reachable = true
            // Applied in the background as well, which is what takes down an activity whose end
            // push never arrived. What made that unsafe was apply() reading the linger off
            // `status` -- still .idle in a freshly woken copy of the app; it now reads it off the
            // running activity instead. Everything else in apply() that shouldn't run off screen
            // (the match alert, starting an activity) guards on `active` itself.
            if let fetched, id == pairID {
                registerCarriedStatus = true
                apply(fetched)
            } else {
                registerCarriedStatus = false
            }
        case .failed:
            reachable = false
            registerCarriedStatus = false
        }
    }

    private func handleReset(_ id: String) {
        guard id == pairID else { return }
        unpair()
        pairingWasReset = true
    }

    // MARK: - State

    private func apply(_ new: QueueStatus) {
        let old = status
        if new.state == .idle {
            // Usually the worker's end push got here first; this covers it not arriving. How long
            // "Not in queue" stays up goes by what the activity is showing, not by `old`, which is
            // still .idle when a silent push has just woken a fresh copy of the app.
            let showing = runningActivity?.content.state ?? old
            endActivities(showingIdleFor: showing.inMatch ? 10 * 60 : showing.state == .queueing ? 60 : 0)
        }
        guard new != old else { return }
        status = new
        if new.state == .found, new.isFreshMatch, active, alertedFoundAt != new.foundAt {
            alertedFoundAt = new.foundAt
            matchAlerts += 1
            // A running Live Activity already plays the match sound from its push.
            alerts.play(sound: !hasRunningActivity)
            watch.sendMatchFound()
        }
        startActivityIfMissing(for: new)
    }

    // MARK: - Live Activity

    private func observe(_ activity: Activity<QueueActivityAttributes>) {
        guard !observedActivities.contains(activity.id) else { return }
        observedActivities.insert(activity.id)
        // Only a running activity clears the others out. One that has already ended would take the
        // live one down with it, which is what happened when a push-to-start woke the app and the
        // previous queue's "Not in queue" was still lingering.
        if activity.activityState == .active {
            for other in Activity<QueueActivityAttributes>.activities where other.id != activity.id {
                Task { await other.end(nil, dismissalPolicy: .immediate) }
            }
        }
        Task {
            for await data in activity.pushTokenUpdates {
                updateToken = data.hex
                updateTokenActivityID = activity.id
                await register()
            }
        }
        Task {
            for await state in activity.activityStateUpdates where state == .ended || state == .dismissed {
                if updateTokenActivityID == activity.id {
                    updateToken = nil
                    updateTokenActivityID = nil
                }
            }
        }
        Task {
            for await content in activity.contentUpdates where active {
                apply(content.state)
            }
        }
    }

    /// Backup for a push-to-start that didn't arrive: start the activity locally, once per queue.
    /// Foreground only, and not by choice: ActivityKit refuses `request` from the background unless
    /// it comes from a LiveActivityIntent, so a silent push can't stand in for a push-to-start.
    private func startActivityIfMissing(for status: QueueStatus) {
        guard active, status.state == .queueing, let startedAt = status.startedAt,
              localActivityQueueStart != startedAt,
              !hasRunningActivity,
              ActivityAuthorizationInfo().areActivitiesEnabled
        else { return }
        localActivityQueueStart = startedAt
        let staleDate = Date().addingTimeInterval(QueueStatus.staleAfter)
        if let activity = try? Activity.request(attributes: QueueActivityAttributes(),
                                                content: .init(state: status, staleDate: staleDate),
                                                pushType: .token) {
            observe(activity)
        }
    }

    private var runningActivity: Activity<QueueActivityAttributes>? {
        Activity<QueueActivityAttributes>.activities.first { $0.activityState == .active }
    }

    private var hasRunningActivity: Bool { runningActivity != nil }

    /// The queue an activity was already started locally for, kept across launches so relaunching
    /// mid-queue doesn't start a second one.
    private var localActivityQueueStart: Double? {
        get { UserDefaults.standard.object(forKey: "localActivityQueueStart") as? Double }
        set { UserDefaults.standard.set(newValue, forKey: "localActivityQueueStart") }
    }

    /// Ends the running activity quietly, leaving "Not in queue" on the lock screen for `seconds`.
    private func endActivities(showingIdleFor seconds: TimeInterval) {
        let content = ActivityContent(state: QueueStatus.idle, staleDate: nil)
        let policy: ActivityUIDismissalPolicy = seconds > 0 ? .after(.now.addingTimeInterval(seconds)) : .immediate
        for activity in Activity<QueueActivityAttributes>.activities where activity.activityState == .active {
            Task { await activity.end(content, dismissalPolicy: policy) }
        }
    }

    private func endAllActivities() {
        for activity in Activity<QueueActivityAttributes>.activities {
            Task { await activity.end(nil, dismissalPolicy: .immediate) }
        }
    }
}

/// Haptics plus sound for a match found while the app is open.
final class MatchAlert {
    private var engine: CHHapticEngine?
    private var player: AVAudioPlayer?

    func play(sound: Bool) {
        playHaptics()
        if sound { playSound() }
    }

    private func playHaptics() {
        guard CHHapticEngine.capabilitiesForHardware().supportsHaptics else {
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            return
        }
        do {
            if engine == nil {
                engine = try CHHapticEngine()
                engine?.resetHandler = { [weak self] in try? self?.engine?.start() }
            }
            try engine?.start()
            var events: [CHHapticEvent] = []
            for index in 0..<3 {
                let time = Double(index) * 0.35
                events.append(CHHapticEvent(eventType: .hapticTransient, parameters: [
                    CHHapticEventParameter(parameterID: .hapticIntensity, value: 1),
                    CHHapticEventParameter(parameterID: .hapticSharpness, value: 0.8),
                ], relativeTime: time))
                events.append(CHHapticEvent(eventType: .hapticContinuous, parameters: [
                    CHHapticEventParameter(parameterID: .hapticIntensity, value: 0.9),
                    CHHapticEventParameter(parameterID: .hapticSharpness, value: 0.4),
                ], relativeTime: time + 0.08, duration: 0.2))
            }
            let player = try engine?.makePlayer(with: CHHapticPattern(events: events, parameters: []))
            try player?.start(atTime: 0)
        } catch {
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }
    }

    private func playSound() {
        guard let url = Bundle.main.url(forResource: "match_found", withExtension: "caf") else { return }
        try? AVAudioSession.sharedInstance().setCategory(.playback, options: [.mixWithOthers])
        try? AVAudioSession.sharedInstance().setActive(true)
        player = try? AVAudioPlayer(contentsOf: url)
        player?.play()
    }
}

/// Hands the pairing to the watch and pokes it when a match is found.
final class WatchBridge: NSObject, WCSessionDelegate {
    private var pendingPairID: String??

    func activate(pairID: String?) {
        guard WCSession.isSupported() else { return }
        pendingPairID = .some(pairID)
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    func send(pairID: String?) {
        guard WCSession.isSupported() else { return }
        guard WCSession.default.activationState == .activated else {
            pendingPairID = .some(pairID)
            return
        }
        try? WCSession.default.updateApplicationContext(["pairID": pairID ?? ""])
    }

    func sendMatchFound() {
        guard WCSession.isSupported(), WCSession.default.isReachable else { return }
        WCSession.default.sendMessage(["matchFound": true], replyHandler: nil, errorHandler: nil)
    }

    func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        guard state == .activated, let pending = pendingPairID else { return }
        pendingPairID = nil
        try? session.updateApplicationContext(["pairID": pending ?? ""])
    }

    func sessionDidBecomeInactive(_ session: WCSession) {}

    func sessionDidDeactivate(_ session: WCSession) {
        session.activate()
    }
}
