import ActivityKit
import Foundation

/// Owns the Live Activity's lifecycle: start it when a queue begins, update it as the
/// phase moves, end it when the queue does.
///
/// Updates are deliberately sparse. Because every phase carries absolute deadlines, the
/// widget ticks its own timers — so this only pushes when the *phase* changes or a
/// meaningful value (the estimate, a vote tally) does, not once a second.
///
/// Two things follow from that sparseness, and both are handled here rather than in the
/// widget. Timestamps are converted onto this device's clock before they're pushed,
/// because the widget process can't reach `QueueStore.clock` to do it itself. And the one
/// moment during a search where the presentation has to change without any new state —
/// the wait running past its estimate — gets a scheduled re-push, since nothing else
/// would redraw it.
@MainActor
public final class LiveActivityController {
    public static let shared = LiveActivityController()

    private var activity: Activity<QueueActivityAttributes>?
    private var lastPushedKind: QueuePhase.Kind?
    private var lastPushedAt: Date = .distantPast
    /// The state currently on screen, already clock-corrected. Re-pushed as-is when the
    /// estimate expires so the widget re-renders against a later `Date.now`.
    private var lastPushedState: QueueActivityAttributes.ContentState?
    private var overdueTask: Task<Void, Never>?
    private var stateObserver: Task<Void, Never>?
    /// The queue session whose activity the user swiped away. Re-requesting one for the
    /// same session would put the banner straight back and read as the app arguing with
    /// them; a genuinely new queue still gets one.
    private var dismissedSession: UUID?

    /// Floor between non-urgent updates. ActivityKit throttles a chatty app, and being
    /// throttled during a match-found alert is the one failure that actually matters.
    private static let minimumInterval: TimeInterval = 2

    // MARK: - Push

    /// Fires once this activity's own push token is known — `AppModel` forwards it to the
    /// PC as `registerActivityPushToken`, which is what lets a background wake-up push
    /// keep this exact activity current without the app process being alive at all.
    public var onActivityPushToken: ((_ sessionID: UUID, _ token: String) -> Void)?
    /// Fires once the app-level push-to-start token is known — forwarded as
    /// `registerActivityStartToken`. Independent of any running activity: this is what
    /// lets the PC create the *next* one from nothing.
    public var onPushToStartToken: ((String) -> Void)?

    private var pushTokenObserver: Task<Void, Never>?
    private var pushToStartTokenObserver: Task<Void, Never>?

    private init() {}

    public var isAvailable: Bool {
        ActivityAuthorizationInfo().areActivitiesEnabled
    }

    /// Takes over an activity left running by a previous launch — including one this
    /// process never started at all, because a push-to-start created it directly while
    /// nothing local was running. Safe to call more than once (a background wake calls
    /// it again on every launch, on the chance a push-to-start happened since the last
    /// one), and a no-op if the activity already adopted is still the newest one.
    ///
    /// A Live Activity deliberately outlives the process that started it, but the
    /// in-memory handle doesn't. Without this, relaunching mid-queue finds `activity ==
    /// nil`, requests a second banner beside the first, and orphans the first with
    /// nothing left alive to end it — which is how a Lock Screen ends up with a stack of
    /// them after a few launches.
    public func adoptRunningActivity() {
        let running = Activity<QueueActivityAttributes>.activities
        guard let newest = running.max(by: { $0.attributes.startedAt < $1.attributes.startedAt })
        else { return }

        // Only one queue can be live at a time, so anything older is a leftover.
        for stale in running where stale.id != newest.id {
            Task { await stale.end(nil, dismissalPolicy: .immediate) }
        }

        guard activity?.id != newest.id else { return }        // already adopted this one

        activity = newest
        lastPushedKind = newest.content.state.phase.kind
        lastPushedState = newest.content.state
        // Whatever it's showing was pushed by a process that's gone; let the first
        // snapshot of this launch through the rate limit to correct it.
        lastPushedAt = .distantPast
        observeState(of: newest)
        observePushToken(of: newest)
        scheduleOverdueRefresh(for: newest.content.state.phase)
    }

    /// Starts listening for the app-level push-to-start token. Call once, independent of
    /// any particular queue — this is what lets the PC create the *next* Live Activity
    /// from nothing, even on a launch that never opens this session's own.
    public func observePushToStartToken() {
        guard pushToStartTokenObserver == nil else { return }
        pushToStartTokenObserver = Task { [weak self] in
            for await tokenData in Activity<QueueActivityAttributes>.pushToStartTokenUpdates {
                guard !Task.isCancelled else { return }
                self?.onPushToStartToken?(tokenData.hexEncoded)
            }
        }
    }

    /// Starts listening for `activity`'s own push token — the async sequence yields
    /// again if the token ever rotates, not just once.
    private func observePushToken(of activity: Activity<QueueActivityAttributes>) {
        pushTokenObserver?.cancel()
        let sessionID = activity.attributes.sessionID
        pushTokenObserver = Task { [weak self] in
            for await tokenData in activity.pushTokenUpdates {
                guard !Task.isCancelled else { return }
                self?.onActivityPushToken?(sessionID, tokenData.hexEncoded)
            }
        }
    }

    /// Reconciles the Live Activity with a snapshot: starts, updates, or ends as needed.
    /// `clock` is the store's drift correction — without it the activity's timers would
    /// run from the server's clock while the app's run from this device's.
    public func sync(to snapshot: QueueSnapshot, clock: ClockSync) {
        let phase = snapshot.phase.localized(with: clock)
        switch phase.kind {
        case .idle, .cancelled:
            end()
        default:
            let state = QueueActivityAttributes.ContentState(phase: phase,
                                                             sequence: snapshot.sequence)
            if activity == nil {
                start(with: state, sessionID: snapshot.sessionID)
            } else {
                update(with: state)
            }
        }
    }

    private func start(with state: QueueActivityAttributes.ContentState, sessionID: UUID) {
        guard isAvailable, sessionID != dismissedSession else { return }
        let attributes = QueueActivityAttributes(sessionID: sessionID, startedAt: .now)
        do {
            // `.token`, not `nil` — without a push token this activity can only ever be
            // updated by this process while it's alive, which is exactly the case this
            // whole push path exists to cover.
            let requested = try Activity.request(
                attributes: attributes,
                content: .init(state: state, staleDate: staleDate(for: state.phase)),
                pushType: .token)
            activity = requested
            observeState(of: requested)
            observePushToken(of: requested)
            record(state)
        } catch {
            activity = nil
        }
    }

    /// Drops the handle once the system has taken the activity away, so the next phase
    /// pushes into a live activity or starts a new one — never into a dead one. A
    /// dismissal is also remembered, so this session doesn't get a replacement banner.
    private func observeState(of activity: Activity<QueueActivityAttributes>) {
        stateObserver?.cancel()
        let sessionID = activity.attributes.sessionID
        let id = activity.id
        stateObserver = Task { [weak self] in
            for await state in activity.activityStateUpdates {
                guard !Task.isCancelled else { return }
                switch state {
                case .dismissed:
                    self?.forget(id, dismissedSession: sessionID)
                case .ended:
                    self?.forget(id, dismissedSession: nil)
                default:
                    break   // `.active` and `.stale` are both still on screen.
                }
            }
        }
    }

    private func forget(_ id: String, dismissedSession: UUID?) {
        if let dismissedSession { self.dismissedSession = dismissedSession }
        guard activity?.id == id else { return }
        activity = nil
        lastPushedKind = nil
        lastPushedState = nil
        overdueTask?.cancel()
        overdueTask = nil
        pushTokenObserver?.cancel()
        pushTokenObserver = nil
    }

    private func update(with state: QueueActivityAttributes.ContentState) {
        guard let activity else { return }
        let kindChanged = lastPushedKind != state.phase.kind
        let elapsed = Date.now.timeIntervalSince(lastPushedAt)
        guard kindChanged || elapsed >= Self.minimumInterval else { return }

        let content = ActivityContent(state: state, staleDate: staleDate(for: state.phase))

        // No alert configuration — the card's own content changing is the signal; a
        // banner on top of it would be a second, redundant notification for the same event.
        Task { await activity.update(content) }
        record(state)
    }

    public func end() {
        overdueTask?.cancel()
        overdueTask = nil
        stateObserver?.cancel()
        stateObserver = nil
        pushTokenObserver?.cancel()
        pushTokenObserver = nil
        lastPushedState = nil
        guard let activity else { return }
        self.activity = nil
        lastPushedKind = nil
        Task { await activity.end(nil, dismissalPolicy: .immediate) }
    }

    // MARK: - Going overdue

    private func record(_ state: QueueActivityAttributes.ContentState) {
        lastPushedKind = state.phase.kind
        lastPushedState = state
        lastPushedAt = .now
        scheduleOverdueRefresh(for: state.phase)
    }

    /// A search that outruns its estimate swaps a filling bar for the indeterminate one
    /// and "Estimated ~3m" for "Longer than usual". On the phone a `TimelineView` notices;
    /// out here nothing does, so wake up at that instant and push the same state again.
    private func scheduleOverdueRefresh(for phase: QueuePhase) {
        overdueTask?.cancel()
        overdueTask = nil

        guard case .searching(let info) = phase,
              let estimate = info.estimatedWait else { return }
        let overdueAt = info.startedAt.addingTimeInterval(estimate)
        let delay = overdueAt.timeIntervalSinceNow
        guard delay > 0 else { return }

        overdueTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard !Task.isCancelled else { return }
            await self?.refreshInPlace()
        }
    }

    /// Re-pushes the state already on screen. The content is unchanged; what changes is
    /// the `Date.now` the widget draws it against.
    private func refreshInPlace() async {
        guard let activity, let state = lastPushedState else { return }
        await activity.update(ActivityContent(state: state,
                                              staleDate: staleDate(for: state.phase)))
        lastPushedAt = .now
    }

    /// After this the system dims the activity as untrustworthy. Tied to the phase's own
    /// deadline so a stalled server visibly goes stale rather than showing a frozen timer
    /// as if it were live.
    private func staleDate(for phase: QueuePhase) -> Date? {
        if let deadline = phase.deadline { return deadline.addingTimeInterval(5) }
        return Date.now.addingTimeInterval(15 * 60)
    }
}
