import Foundation

/// A map's objective type. The OverFast catalog reports these in a map's `gamemodes`.
public enum MapType: String, Codable, Hashable, Sendable {
    case escort, hybrid, control, push, flashpoint, clash, assault
    case elimination, deathmatch
    case teamDeathmatch = "team-deathmatch"
    case captureTheFlag = "capture-the-flag"
    case payloadRace = "payload-race"
    case practiceRange = "practice-range"
    case workshop
    case unknown

    public var displayName: String {
        switch self {
        case .escort: return "Escort"
        case .hybrid: return "Hybrid"
        case .control: return "Control"
        case .push: return "Push"
        case .flashpoint: return "Flashpoint"
        case .clash: return "Clash"
        case .assault: return "Assault"
        case .elimination: return "Elimination"
        case .deathmatch: return "Deathmatch"
        case .teamDeathmatch: return "Team Deathmatch"
        case .captureTheFlag: return "Capture the Flag"
        case .payloadRace: return "Payload Race"
        case .practiceRange: return "Practice Range"
        case .workshop: return "Workshop"
        case .unknown: return "Unknown"
        }
    }

    public var symbolName: String {
        switch self {
        case .escort: return "truck.box.fill"
        case .hybrid: return "arrow.triangle.branch"
        case .control: return "circle.circle.fill"
        case .push: return "figure.walk.motion"
        case .flashpoint: return "flame.fill"
        case .clash: return "bolt.horizontal.fill"
        case .assault: return "square.split.2x1.fill"
        default: return "map.fill"
        }
    }

    /// The rotation a given queue draws from.
    public static func pool(for mode: QueueMode) -> Set<MapType> {
        switch mode {
        case .competitive, .quickPlay, .stadium:
            return [.escort, .hybrid, .control, .push, .flashpoint, .clash]
        case .arcade, .mysteryHeroes, .custom:
            return [.escort, .hybrid, .control, .push, .flashpoint, .clash,
                    .assault, .elimination, .deathmatch, .teamDeathmatch,
                    .captureTheFlag, .payloadRace]
        }
    }
}

/// A map as published by the OverFast catalog.
public struct OverwatchMap: Codable, Hashable, Identifiable, Sendable {
    public let key: String
    public let name: String
    public let screenshot: URL?
    public let gamemodes: [MapType]
    public let location: String?
    public let countryCode: String?

    public var id: String { key }

    public var primaryType: MapType { gamemodes.first ?? .unknown }

    public init(key: String, name: String, screenshot: URL?, gamemodes: [MapType],
                location: String? = nil, countryCode: String? = nil) {
        self.key = key
        self.name = name
        self.screenshot = screenshot
        self.gamemodes = gamemodes
        self.location = location
        self.countryCode = countryCode
    }

    enum CodingKeys: String, CodingKey {
        case key, name, screenshot, gamemodes, location
        case countryCode = "country_code"
    }

    /// Unrecognised gamemode strings decode to `.unknown` instead of throwing, so a
    /// new mode shipping in Overwatch can't empty the whole map list.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        name = try c.decode(String.self, forKey: .name)
        screenshot = try c.decodeIfPresent(URL.self, forKey: .screenshot)
        let raw = try c.decodeIfPresent([String].self, forKey: .gamemodes) ?? []
        gamemodes = raw.map { MapType(rawValue: $0) ?? .unknown }
        location = try c.decodeIfPresent(String.self, forKey: .location)
        countryCode = try c.decodeIfPresent(String.self, forKey: .countryCode)
    }

    /// Flag emoji derived from the ISO country code, for the map card's corner.
    public var flag: String? {
        guard let countryCode, countryCode.count == 2 else { return nil }
        return String(String.UnicodeScalarView(countryCode.uppercased().unicodeScalars.compactMap {
            Unicode.Scalar(127397 + $0.value)
        }))
    }
}
