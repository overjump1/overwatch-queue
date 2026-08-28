import Foundation

/// The title/body pair shown for a phase's alert — shared so the phone's Live Activity
/// alert and the watch's local notification never drift apart in wording.
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
