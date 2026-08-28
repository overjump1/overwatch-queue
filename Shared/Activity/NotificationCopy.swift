import Foundation

/// The title/body pair for an urgent phase's Live Activity alert — the sound/haptic/peek
/// a delivery app gives you for "your order is on the way," not a separate notification.
/// Mirrors `protocol.notification_copy` in `server/owqserver/protocol.py`, duplicated on
/// purpose since the two live in different languages.
public enum NotificationCopy {
    public static func title(for phase: QueuePhase) -> String {
        switch phase.kind {
        case .matchFound: return "Match Found"
        case .mapVote: return "Map Vote"
        case .heroSelect: return "Hero Select"
        default: return "Overwatch Queue"
        }
    }

    public static func body(for phase: QueuePhase) -> String {
        switch phase.kind {
        case .matchFound: return "You're being pulled into the game — get back to your PC."
        case .mapVote: return "Pick where you want to play."
        case .heroSelect: return "Choose your hero."
        default: return "Status changed."
        }
    }
}
