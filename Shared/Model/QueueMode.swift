import Foundation

/// What the player queued for. Distinct from `MapType`, which describes a map's objective.
public enum QueueMode: String, Codable, CaseIterable, Hashable, Sendable {
    case quickPlay
    case competitive
    case arcade
    case stadium
    case mysteryHeroes
    case custom

    public var displayName: String {
        switch self {
        case .quickPlay: return "Quick Play"
        case .competitive: return "Competitive"
        case .arcade: return "Arcade"
        case .stadium: return "Stadium"
        case .mysteryHeroes: return "Mystery Heroes"
        case .custom: return "Custom Game"
        }
    }

    /// Whether the player picks a role before queueing.
    public var usesRoleQueue: Bool {
        switch self {
        case .competitive, .quickPlay, .stadium: return true
        case .arcade, .mysteryHeroes, .custom: return false
        }
    }

    /// Whether the player chooses a hero at all (Mystery Heroes assigns one).
    public var allowsHeroSelect: Bool {
        self != .mysteryHeroes
    }

    /// The `gamemodes` value the OverFast hero catalog uses for this queue, if any.
    public var catalogKey: String? {
        switch self {
        case .quickPlay, .competitive, .arcade, .mysteryHeroes, .custom: return "quickplay"
        case .stadium: return "stadium"
        }
    }

    public var symbolName: String {
        switch self {
        case .quickPlay: return "bolt.fill"
        case .competitive: return "trophy.fill"
        case .arcade: return "gamecontroller.fill"
        case .stadium: return "building.columns.fill"
        case .mysteryHeroes: return "questionmark.diamond.fill"
        case .custom: return "slider.horizontal.3"
        }
    }
}
