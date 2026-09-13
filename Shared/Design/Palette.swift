import SwiftUI

/// The app's colour language. Overwatch's identity is a warm orange signal on a cold
/// blue field — the UI leans on that contrast to say "waiting" versus "act now" without
/// needing words.
public enum Palette {
    public static let orange = Color(red: 0.976, green: 0.620, blue: 0.102)   // #F99E1A
    public static let amber = Color(red: 1.0, green: 0.780, blue: 0.298)
    public static let blue = Color(red: 0.129, green: 0.353, blue: 0.588)     // #215A96
    public static let deepBlue = Color(red: 0.051, green: 0.098, blue: 0.180)
    public static let night = Color(red: 0.031, green: 0.043, blue: 0.078)
    public static let white = Color(red: 0.965, green: 0.973, blue: 0.988)

    public static let tank = Color(red: 0.404, green: 0.616, blue: 0.851)
    public static let damage = Color(red: 0.918, green: 0.427, blue: 0.325)
    public static let support = Color(red: 0.443, green: 0.780, blue: 0.494)
    public static let neutral = Color(red: 0.612, green: 0.647, blue: 0.729)

    // One colour per queue. Quick Play and Competitive follow the game's own queue banners
    // — the same hues `queuemodes.json` measures them by, blue and pink.
    public static let quickPlay = Color(red: 0.231, green: 0.580, blue: 0.965)     // #3B94F6
    public static let competitive = Color(red: 0.925, green: 0.278, blue: 0.494)   // #EC477E
    public static let arcade = Color(red: 0.302, green: 0.800, blue: 0.518)        // #4DCC84
    public static let stadium = amber
    public static let mysteryHeroes = Color(red: 0.639, green: 0.463, blue: 0.957) // #A376F4

    public static func tint(for mode: QueueMode) -> Color {
        switch mode {
        case .quickPlay: return quickPlay
        case .competitive: return competitive
        case .arcade: return arcade
        case .stadium: return stadium
        case .mysteryHeroes: return mysteryHeroes
        case .custom: return neutral
        }
    }

    public static func tint(for role: Role) -> Color {
        switch role {
        case .tank: return tank
        case .damage: return damage
        case .support: return support
        case .flex, .open: return orange
        }
    }

    /// The accent for a phase — this is what shifts the whole screen's mood as the queue
    /// progresses, from calm blue to an urgent orange.
    public static func accent(for phase: QueuePhase) -> Color {
        switch phase {
        case .idle: return neutral
        case .searching(let i): return tint(for: i.role)
        case .matchFound: return orange
        case .mapVote: return amber
        case .heroSelect: return orange
        case .inGame: return support
        case .cancelled: return neutral
        }
    }

    /// The nine control-point colours of the animated background mesh, in row order.
    ///
    /// Every phase keeps the corners dark and pools colour in the centre. Flooding the
    /// screen with the accent — the obvious way to make match-found feel loud — actually
    /// makes it *quieter*, because white text loses its contrast and the burst has no
    /// darkness left to be bright against. So match-found gets a hotter core, not a
    /// brighter field.
    public static func mesh(for phase: QueuePhase) -> [Color] {
        let accent = accent(for: phase)
        switch phase.kind {
        case .matchFound:
            return [night,                  orange.opacity(0.42), night,
                    orange.opacity(0.34),   amber.opacity(0.85),  orange.opacity(0.34),
                    night,                  orange.opacity(0.30), night]

        case .idle, .cancelled:
            return [night,                  deepBlue.opacity(0.45), night,
                    deepBlue.opacity(0.40), deepBlue.opacity(0.65), deepBlue.opacity(0.40),
                    night,                  deepBlue.opacity(0.35), night]

        default:
            return [night,                  deepBlue.opacity(0.75), night,
                    deepBlue.opacity(0.65), accent.opacity(0.42),   deepBlue.opacity(0.65),
                    night,                  deepBlue.opacity(0.55), night]
        }
    }
}

public extension Role {
    var tint: Color { Palette.tint(for: self) }
}

public extension QueueMode {
    var tint: Color { Palette.tint(for: self) }
}

public extension QueuePhase {
    /// The big colour on a glanceable surface: the queue's own mode where the phase knows
    /// it, so a Competitive queue stays pink from search to match. The phase's accent only
    /// for the phases that carry no mode at all.
    var modeTint: Color { mode?.tint ?? Palette.accent(for: self) }

    /// The big icon to go with `modeTint`.
    var modeSymbolName: String { mode?.symbolName ?? statusSymbolName }
}
