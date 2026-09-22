import SwiftUI

enum QueueState: String, Codable, Hashable {
    /// `playing` is what `found` turns into a minute after the match is found.
    case idle, queueing, found, playing
}

enum GameMode: String, Codable, Hashable, CaseIterable {
    case quickPlay, competitive, arcade, stadium, mysteryHeroes, custom

    var name: String {
        switch self {
        case .quickPlay: "Quick Play"
        case .competitive: "Competitive"
        case .arcade: "Arcade"
        case .stadium: "Stadium"
        case .mysteryHeroes: "Mystery Heroes"
        case .custom: "Custom Game"
        }
    }

    var symbol: String {
        switch self {
        case .quickPlay: "bolt.fill"
        case .competitive: "trophy.fill"
        case .arcade: "gamecontroller.fill"
        case .stadium: "building.columns.fill"
        case .mysteryHeroes: "questionmark.diamond.fill"
        case .custom: "slider.horizontal.3"
        }
    }

    var color: Color {
        switch self {
        case .quickPlay: Color(red: 0.231, green: 0.580, blue: 0.965)
        case .competitive: Color(red: 0.925, green: 0.278, blue: 0.494)
        case .arcade: Color(red: 0.302, green: 0.800, blue: 0.518)
        case .stadium: Color(red: 1.0, green: 0.780, blue: 0.298)
        case .mysteryHeroes: Color(red: 0.639, green: 0.463, blue: 0.957)
        case .custom: Color(white: 0.62)
        }
    }
}

/// A match counts as under way a minute after it's found. The worker works to the same minute
/// (`PLAYING_AFTER_SECONDS` in worker/src/logic.ts), so a queue the phone times on its own reads
/// exactly like one a PC is driving.
let playingAfterFoundSeconds: TimeInterval = 60

/// The queue as the worker reports it. Also the Live Activity's content state.
struct QueueStatus: Codable, Hashable {
    var state: QueueState
    var mode: GameMode?
    var startedAt: Double?
    var foundAt: Double?

    static let idle = QueueStatus(state: .idle)

    init(state: QueueState, mode: GameMode? = nil, startedAt: Double? = nil, foundAt: Double? = nil) {
        self.state = state
        self.mode = mode
        self.startedAt = startedAt
        self.foundAt = foundAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        state = (try? container.decode(QueueState.self, forKey: .state)) ?? .idle
        mode = try? container.decodeIfPresent(GameMode.self, forKey: .mode)
        startedAt = try? container.decodeIfPresent(Double.self, forKey: .startedAt)
        foundAt = try? container.decodeIfPresent(Double.self, forKey: .foundAt)
    }

    var startDate: Date? { startedAt.map { Date(timeIntervalSince1970: $0) } }
    var foundDate: Date? { foundAt.map { Date(timeIntervalSince1970: $0) } }

    var title: String {
        switch state {
        case .idle: "Not in queue"
        case .queueing: "In queue"
        case .found: "Match found!"
        case .playing: "In a match"
        }
    }

    var subtitle: String {
        switch state {
        case .idle: "Start a queue on your PC"
        case .queueing, .found: mode?.name ?? "Overwatch"
        case .playing: "Good luck, have fun!"
        }
    }

    var accent: Color {
        switch state {
        case .idle: Color(white: 0.5)
        case .queueing, .playing: mode?.color ?? .white
        case .found: .green
        }
    }

    var inMatch: Bool { state == .found || state == .playing }

    var waited: TimeInterval? {
        guard let startedAt, let foundAt else { return nil }
        return max(0, foundAt - startedAt)
    }

    /// True for a match found moments ago, so opening the app later doesn't re-alert.
    var isFreshMatch: Bool {
        guard state == .found, let foundAt else { return false }
        return Date().timeIntervalSince1970 - foundAt < 60
    }
}

func clockString(_ seconds: TimeInterval) -> String {
    let total = max(0, Int(seconds))
    if total >= 3600 {
        return String(format: "%d:%02d:%02d", total / 3600, total / 60 % 60, total % 60)
    }
    return String(format: "%d:%02d", total / 60, total % 60)
}
