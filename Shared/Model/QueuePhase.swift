import Foundation

/// Where the player is in the queue → match flow. One enum drives the phone UI, the
/// Live Activity, the watch app and every widget, so all surfaces stay in lockstep.
///
/// Every phase carries **absolute deadlines rather than remaining seconds**. Clients
/// render them with `TimelineView`/`Text(timerInterval:)` and tick locally, which is what
/// lets a Live Activity count up on the Lock Screen without spending any update budget.
public enum QueuePhase: Codable, Hashable, Sendable {
    case idle
    case searching(SearchInfo)
    case matchFound(MatchFoundInfo)
    case mapVote(MapVoteInfo)
    case heroSelect(HeroSelectInfo)
    case inGame(InGameInfo)
    case cancelled(CancelInfo)
}

public struct SearchInfo: Codable, Hashable, Sendable {
    public var mode: QueueMode
    public var role: Role
    public var startedAt: Date
    /// Server's estimate of total wait. Nil when it has no idea yet.
    public var estimatedWait: TimeInterval?
    public var groupSize: Int

    public init(mode: QueueMode, role: Role, startedAt: Date,
                estimatedWait: TimeInterval? = nil, groupSize: Int = 1) {
        self.mode = mode
        self.role = role
        self.startedAt = startedAt
        self.estimatedWait = estimatedWait
        self.groupSize = groupSize
    }

    public func elapsed(at now: Date = .now) -> TimeInterval {
        max(0, now.timeIntervalSince(startedAt))
    }

    /// The moment the estimate runs out, as an absolute date. Surfaces that can't
    /// recompute a fraction every second — a Live Activity's bar, above all — measure the
    /// wait against this instead of against `progress()`.
    public var estimatedEnd: Date? {
        guard let estimatedWait, estimatedWait > 0 else { return nil }
        return startedAt.addingTimeInterval(estimatedWait)
    }

    /// 0…1 against the estimate, clamped. Nil when there is no estimate to measure against.
    public func progress(at now: Date = .now) -> Double? {
        guard let estimatedWait, estimatedWait > 0 else { return nil }
        return min(1, elapsed(at: now) / estimatedWait)
    }

    /// True once the wait has run past the estimate — the UI shifts to a different
    /// treatment here rather than pinning a full bar and lying about it.
    public func isOverdue(at now: Date = .now) -> Bool {
        guard let estimatedWait else { return false }
        return elapsed(at: now) > estimatedWait
    }
}

public struct MatchFoundInfo: Codable, Hashable, Sendable {
    public var mode: QueueMode
    public var role: Role
    /// How long the player waited, frozen at the moment the match landed.
    public var waited: TimeInterval
    /// When the game pulls you in. There is no accept prompt in Overwatch 2 — this is
    /// how long you have to get back to the PC, and the last moment a cancel might
    /// still land.
    public var lockInAt: Date

    public init(mode: QueueMode, role: Role, waited: TimeInterval, lockInAt: Date) {
        self.mode = mode
        self.role = role
        self.waited = waited
        self.lockInAt = lockInAt
    }
}

public struct MapOption: Codable, Hashable, Identifiable, Sendable {
    public var mapKey: String
    public var votes: Int

    public var id: String { mapKey }

    public init(mapKey: String, votes: Int = 0) {
        self.mapKey = mapKey
        self.votes = votes
    }
}

public struct MapVoteInfo: Codable, Hashable, Sendable {
    public var options: [MapOption]
    public var deadline: Date
    /// The local player's pick, once made.
    public var myVote: String?

    public init(options: [MapOption], deadline: Date, myVote: String? = nil) {
        self.options = options
        self.deadline = deadline
        self.myVote = myVote
    }

    public var totalVotes: Int { options.reduce(0) { $0 + $1.votes } }

    public func share(of option: MapOption) -> Double {
        totalVotes > 0 ? Double(option.votes) / Double(totalVotes) : 0
    }

    public var leader: MapOption? {
        options.max { $0.votes < $1.votes }
    }
}

/// One player's pick, as read off the hero-select screen.
///
/// `slot` is a position in the row of portraits, nothing more. It is not an identity and
/// not a role — role queue orders the slots by role, so the player is not reliably in the
/// first one. `isSelf` is the only thing that says which pick is ours, and the server
/// leaves it false on every pick when it couldn't tell rather than guess at a slot.
public struct TeamPick: Codable, Hashable, Sendable, Identifiable {
    public var slot: Int
    public var heroKey: String
    public var isSelf: Bool

    public var id: Int { slot }

    public init(slot: Int, heroKey: String, isSelf: Bool = false) {
        self.slot = slot
        self.heroKey = heroKey
        self.isSelf = isSelf
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        slot = try container.decode(Int.self, forKey: .slot)
        heroKey = try container.decode(String.self, forKey: .heroKey)
        // Absent from a server that scanned but couldn't tell whose pick is whose.
        isSelf = try container.decodeIfPresent(Bool.self, forKey: .isSelf) ?? false
    }
}

public struct HeroSelectInfo: Codable, Hashable, Sendable {
    public var mode: QueueMode
    public var role: Role
    public var mapKey: String?
    public var deadline: Date
    /// Heroes already locked by teammates.
    public var takenHeroKeys: [String]
    public var myHeroKey: String?
    /// The heroes the server actually saw on the roster, or nil if it never looked.
    ///
    /// Nil and empty mean different things and the difference matters here: nil is "no
    /// one read the screen" — no game running, or a server without the vision extras —
    /// and the app should show its full catalog. Empty would be "the roster is genuinely
    /// empty", which is not a thing that happens.
    public var availableHeroKeys: [String]?
    /// What each player has picked so far, or nil if the server never looked.
    public var teamPicks: [TeamPick]?

    public init(mode: QueueMode, role: Role, mapKey: String? = nil, deadline: Date,
                takenHeroKeys: [String] = [], myHeroKey: String? = nil,
                availableHeroKeys: [String]? = nil, teamPicks: [TeamPick]? = nil) {
        self.mode = mode
        self.role = role
        self.mapKey = mapKey
        self.deadline = deadline
        self.takenHeroKeys = takenHeroKeys
        self.myHeroKey = myHeroKey
        self.availableHeroKeys = availableHeroKeys
        self.teamPicks = teamPicks
    }
}

public struct InGameInfo: Codable, Hashable, Sendable {
    public var mode: QueueMode
    public var mapKey: String?
    public var heroKey: String?
    public var startedAt: Date

    public init(mode: QueueMode, mapKey: String? = nil, heroKey: String? = nil, startedAt: Date) {
        self.mode = mode
        self.mapKey = mapKey
        self.heroKey = heroKey
        self.startedAt = startedAt
    }
}

public struct CancelInfo: Codable, Hashable, Sendable {
    public enum Reason: String, Codable, Hashable, Sendable {
        case userLeft
        case matchCancelled     // the game dropped the match
        case timedOut
        case serverError
    }

    public var reason: Reason
    public var message: String?

    public init(reason: Reason, message: String? = nil) {
        self.reason = reason
        self.message = message
    }

    public var displayText: String {
        if let message, !message.isEmpty { return message }
        switch reason {
        case .userLeft: return "You left the queue"
        case .matchCancelled: return "Match fell apart — requeueing"
        case .timedOut: return "Match timed out"
        case .serverError: return "Lost contact with the game"
        }
    }
}

// MARK: - Discriminator

public extension QueuePhase {
    /// Case identity without the payload — for animation keys, transition legality and
    /// cheap equality checks where the associated values would cause false churn.
    enum Kind: String, Codable, Hashable, Sendable, CaseIterable {
        case idle, searching, matchFound, mapVote, heroSelect, inGame, cancelled
    }

    var kind: Kind {
        switch self {
        case .idle: return .idle
        case .searching: return .searching
        case .matchFound: return .matchFound
        case .mapVote: return .mapVote
        case .heroSelect: return .heroSelect
        case .inGame: return .inGame
        case .cancelled: return .cancelled
        }
    }

    /// True while the player is waiting on the game rather than acting.
    var isWaiting: Bool { kind == .searching }

    /// Phases that demand the player's attention right now — these drive alerts,
    /// haptics and Live Activity `AlertConfiguration`s.
    var isUrgent: Bool {
        switch kind {
        case .matchFound, .mapVote, .heroSelect: return true
        default: return false
        }
    }

    /// The deadline the player is racing, if this phase has one.
    var deadline: Date? {
        switch self {
        case .matchFound(let i): return i.lockInAt
        case .mapVote(let i): return i.deadline
        case .heroSelect(let i): return i.deadline
        default: return nil
        }
    }

    var mode: QueueMode? {
        switch self {
        case .searching(let i): return i.mode
        case .matchFound(let i): return i.mode
        case .heroSelect(let i): return i.mode
        case .inGame(let i): return i.mode
        case .mapVote, .idle, .cancelled: return nil
        }
    }

    var role: Role? {
        switch self {
        case .searching(let i): return i.role
        case .matchFound(let i): return i.role
        case .heroSelect(let i): return i.role
        default: return nil
        }
    }

    /// Whether a transition is one the flow actually permits. The mock driver and the
    /// store both check this, so a bad server message can't wedge the UI in a
    /// nonsensical state.
    func canTransition(to next: QueuePhase) -> Bool {
        switch (kind, next.kind) {
        case (_, .cancelled), (_, .idle):            return true
        case (.idle, .searching):                     return true
        case (.searching, .matchFound):               return true
        case (.matchFound, .mapVote),
             (.matchFound, .heroSelect),
             (.matchFound, .inGame),                                // Mystery Heroes: straight in
             (.matchFound, .searching):               return true   // match fell apart → requeue
        case (.mapVote, .heroSelect):                 return true
        case (.mapVote, .inGame):                     return true
        case (.heroSelect, .inGame):                  return true
        case (.cancelled, .searching):                return true
        case let (a, b) where a == b:                 return true   // payload refresh
        default:                                      return false
        }
    }
}

// MARK: - Clock correction

public extension QueuePhase {
    /// The same phase with every timestamp moved onto this device's clock.
    ///
    /// The app reads its dates back through `QueueStore.clock`, but a widget process has
    /// no access to that correction — it can only compare what it's handed against a bare
    /// `Date.now`. Anything crossing into one (the Live Activity above all) has to be
    /// converted first, or its timers and bars run from a different origin than the
    /// screen they're mirroring.
    func localized(with clock: ClockSync) -> QueuePhase {
        switch self {
        case .idle, .cancelled:
            return self
        case .searching(var i):
            i.startedAt = clock.toLocal(i.startedAt)
            return .searching(i)
        case .matchFound(var i):
            i.lockInAt = clock.toLocal(i.lockInAt)
            return .matchFound(i)
        case .mapVote(var i):
            i.deadline = clock.toLocal(i.deadline)
            return .mapVote(i)
        case .heroSelect(var i):
            i.deadline = clock.toLocal(i.deadline)
            return .heroSelect(i)
        case .inGame(var i):
            i.startedAt = clock.toLocal(i.startedAt)
            return .inGame(i)
        }
    }
}
