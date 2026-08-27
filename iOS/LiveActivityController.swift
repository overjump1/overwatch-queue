import ActivityKit
import Foundation

/// Owns the Live Activity's lifecycle: start it when a queue begins, update it as the
/// phase moves, end it when the queue does.
///
/// Updates are deliberately sparse. Because every phase carries absolute deadlines, the
/// widget ticks its own timers — so this only pushes when the *phase* changes or a
/// meaningful value (the estimate, a vote tally) does, not once a second.
@MainActor
public final class LiveActivityController {
    public static let shared = LiveActivityController()

    private var activity: Activity<QueueActivityAttributes>?
    private var lastPushedKind: QueuePhase.Kind?
    private var lastPushedAt: Date = .distantPast

    /// Floor between non-urgent updates. ActivityKit throttles a chatty app, and being
    /// throttled during a match-found alert is the one failure that actually matters.
    private static let minimumInterval: TimeInterval = 2

    private init() {}

    public var isAvailable: Bool {
        ActivityAuthorizationInfo().areActivitiesEnabled
    }

    /// Reconciles the Live Activity with a snapshot: starts, updates, or ends as needed.
    public func sync(to snapshot: QueueSnapshot) {
        switch snapshot.phase.kind {
        case .idle, .cancelled:
            end()
        default:
            if activity == nil {
                start(with: snapshot)
            } else {
                update(with: snapshot)
            }
        }
    }

    private func start(with snapshot: QueueSnapshot) {
        guard isAvailable else { return }
        let attributes = QueueActivityAttributes(sessionID: snapshot.sessionID, startedAt: .now)
        let state = QueueActivityAttributes.ContentState(phase: snapshot.phase,
                                                         sequence: snapshot.sequence)
        do {
            activity = try Activity.request(
                attributes: attributes,
                content: .init(state: state, staleDate: staleDate(for: snapshot.phase)),
                pushType: nil)
            lastPushedKind = snapshot.phase.kind
            lastPushedAt = .now
        } catch {
            activity = nil
        }
    }

    private func update(with snapshot: QueueSnapshot) {
        guard let activity else { return }
        let kindChanged = lastPushedKind != snapshot.phase.kind
        let elapsed = Date.now.timeIntervalSince(lastPushedAt)
        guard kindChanged || elapsed >= Self.minimumInterval else { return }

        let state = QueueActivityAttributes.ContentState(phase: snapshot.phase,
                                                         sequence: snapshot.sequence)
        let content = ActivityContent(state: state, staleDate: staleDate(for: snapshot.phase))

        Task {
            // Only an urgent phase gets an alert — that's what expands the Dynamic Island
            // and buzzes the paired watch. Alerting on every tally change would train the
            // user to ignore it.
            if kindChanged, snapshot.phase.isUrgent {
                await activity.update(content, alertConfiguration: alert(for: snapshot.phase))
            } else {
                await activity.update(content)
            }
        }
        lastPushedKind = snapshot.phase.kind
        lastPushedAt = .now
    }

    public func end() {
        guard let activity else { return }
        self.activity = nil
        lastPushedKind = nil
        Task { await activity.end(nil, dismissalPolicy: .immediate) }
    }

    /// After this the system dims the activity as untrustworthy. Tied to the phase's own
    /// deadline so a stalled server visibly goes stale rather than showing a frozen timer
    /// as if it were live.
    private func staleDate(for phase: QueuePhase) -> Date? {
        if let deadline = phase.deadline { return deadline.addingTimeInterval(5) }
        return Date.now.addingTimeInterval(15 * 60)
    }

    private func alert(for phase: QueuePhase) -> AlertConfiguration {
        switch phase.kind {
        case .matchFound:
            return AlertConfiguration(title: "Match Found",
                                      body: "You're being pulled into the game — get back to your PC.",
                                      sound: .default)
        case .mapVote:
            return AlertConfiguration(title: "Map Vote",
                                      body: "Pick where you want to play.",
                                      sound: .default)
        case .heroSelect:
            return AlertConfiguration(title: "Hero Select",
                                      body: "Choose your hero.",
                                      sound: .default)
        default:
            return AlertConfiguration(title: "Overwatch Queue", body: "Status changed.", sound: .default)
        }
    }
}
