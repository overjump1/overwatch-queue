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

    private var fcmToken: String?
    private var startToken: String?
    private var updateToken: String?
    /// The activity `updateToken` belongs to, so the token is dropped once that activity ends.
    private var updateTokenActivityID: String?
    private var active = false
    private var launched = false
    private var pollTask: Task<Void, Never>?
    private var observedActivities = Set<String>()
    private var localActivityQueueStart: Double?
    /// The match we already alerted for, so the same match never alerts twice.
    private var alertedFoundAt: Double?
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
        if let newest = Activity<QueueActivityAttributes>.activities.last {
            observe(newest)
        }
    }

    /// A silent push from the worker woke the app: report the current tokens.
    func refreshTokens() async {
        launch()
        await register()
    }

    func setActive(_ isActive: Bool) {
        active = isActive
        pollTask?.cancel()
        pollTask = nil
        guard isActive else { return }
        launch()
        pollTask = Task {
            await register()
            while !Task.isCancelled {
                await refresh()
                try? await Task.sleep(for: .seconds(2))
            }
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
        var body: [String: Any] = [
            "kind": "phone",
            "activitiesEnabled": ActivityAuthorizationInfo().areActivitiesEnabled,
            "activityRunning": hasRunningActivity,
        ]
        if let fcmToken { body["fcm"] = fcmToken }
        if let startToken { body["startToken"] = startToken }
        if let updateToken { body["updateToken"] = updateToken }
        if case .reset = await Worker.register(pairID: id, body: body) {
            handleReset(id)
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
            // Usually the worker's end push got here first; this covers it not arriving.
            endActivities(showingIdleFor: old.inMatch ? 10 * 60 : old.state == .queueing ? 60 : 0)
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
        for other in Activity<QueueActivityAttributes>.activities where other.id != activity.id {
            Task { await other.end(nil, dismissalPolicy: .immediate) }
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
    private func startActivityIfMissing(for status: QueueStatus) {
        guard active, status.state == .queueing, let startedAt = status.startedAt,
              localActivityQueueStart != startedAt,
              !hasRunningActivity,
              ActivityAuthorizationInfo().areActivitiesEnabled
        else { return }
        localActivityQueueStart = startedAt
        if let activity = try? Activity.request(attributes: QueueActivityAttributes(),
                                                content: .init(state: status, staleDate: nil),
                                                pushType: .token) {
            observe(activity)
        }
    }

    private var hasRunningActivity: Bool {
        Activity<QueueActivityAttributes>.activities.contains { $0.activityState == .active }
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
