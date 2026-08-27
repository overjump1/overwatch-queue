import Foundation

/// A queueable role. `flex` and `open` are queue selections rather than hero roles —
/// the OverFast catalog only ever reports tank/damage/support for a hero.
public enum Role: String, Codable, CaseIterable, Hashable, Sendable {
    case tank
    case damage
    case support
    case flex
    case open

    /// Roles a hero can actually have (i.e. what the catalog returns).
    public static let heroRoles: [Role] = [.tank, .damage, .support]

    /// Roles you can pick when entering a role queue.
    public static let queueable: [Role] = [.tank, .damage, .support, .flex]

    public var displayName: String {
        switch self {
        case .tank: return "Tank"
        case .damage: return "Damage"
        case .support: return "Support"
        case .flex: return "Flex"
        case .open: return "Open Queue"
        }
    }

    /// Short form for tight spaces (Dynamic Island, watch complications).
    public var abbreviation: String {
        switch self {
        case .tank: return "TANK"
        case .damage: return "DPS"
        case .support: return "SUP"
        case .flex: return "FLEX"
        case .open: return "OPEN"
        }
    }

    public var symbolName: String {
        switch self {
        case .tank: return "shield.fill"
        case .damage: return "scope"
        case .support: return "cross.case.fill"
        case .flex: return "arrow.triangle.2.circlepath"
        case .open: return "person.3.fill"
        }
    }
}
