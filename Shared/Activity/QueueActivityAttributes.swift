import Foundation
import SwiftUI
#if canImport(ActivityKit)
import ActivityKit
#endif

#if canImport(ActivityKit) && os(iOS)

/// The Live Activity's data contract, shared by the app (which starts and updates it) and
/// the widget extension (which draws it).
///
/// `ContentState` is intentionally small and free of ticking values: it carries the phase
/// and its absolute deadlines, and the widget derives elapsed time locally with
/// `Text(timerInterval:)`. A queue can then run for ten minutes on one update instead of
/// six hundred.
public struct QueueActivityAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable {
        public var phase: QueuePhase
        /// Mirrors `QueueSnapshot.sequence` so a late-arriving update can be ignored.
        public var sequence: Int

        public init(phase: QueuePhase, sequence: Int) {
            self.phase = phase
            self.sequence = sequence
        }
    }

    /// Fixed for the life of the activity.
    public var sessionID: UUID
    public var startedAt: Date

    public init(sessionID: UUID, startedAt: Date) {
        self.sessionID = sessionID
        self.startedAt = startedAt
    }
}

public extension QueueActivityAttributes.ContentState {
    /// How the Dynamic Island's minimal and compact presentations summarise the phase.
    var shortStatus: String {
        switch phase {
        case .idle: return "Idle"
        case .searching(let i): return i.role.abbreviation
        case .matchFound: return "FOUND"
        case .mapVote: return "VOTE"
        case .heroSelect: return "PICK"
        case .inGame: return "LIVE"
        case .cancelled: return "ENDED"
        }
    }

    /// What's happening. The mode is the icon and the colour, so this doesn't repeat it.
    var headline: String { phase.statusHeadline }

    /// The queue this card is about — "Competitive" — or nil where the phase doesn't say.
    var detail: String? { phase.mode?.displayName }

    /// The big icon: the queue's mode.
    var symbolName: String { phase.modeSymbolName }

    /// The card's colour: the queue's mode.
    var accent: Color { phase.modeTint }

    /// The small icons: every role queued for.
    var roles: [Role] { phase.roles.filter { $0 != .open } }
}

#endif
