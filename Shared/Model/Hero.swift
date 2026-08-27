import Foundation

/// A hero as published by the OverFast catalog. `portrait` points at Blizzard's CDN.
public struct Hero: Codable, Hashable, Identifiable, Sendable {
    public let key: String
    public let name: String
    public let portrait: URL?
    public let role: Role
    public let subrole: String?
    public let gamemodes: [String]?

    public var id: String { key }

    public init(key: String, name: String, portrait: URL?, role: Role,
                subrole: String? = nil, gamemodes: [String]? = nil) {
        self.key = key
        self.name = name
        self.portrait = portrait
        self.role = role
        self.subrole = subrole
        self.gamemodes = gamemodes
    }

    /// Heroes are unavailable in some queues (e.g. the Stadium roster is a subset).
    public func isAvailable(in mode: QueueMode) -> Bool {
        guard let gamemodes, let key = mode.catalogKey else { return true }
        return gamemodes.contains(key)
    }

    /// Initials used by the placeholder tile when art hasn't loaded.
    public var initials: String {
        let words = name.split(whereSeparator: { $0 == " " || $0 == "." || $0 == ":" })
        let letters = words.prefix(2).compactMap(\.first)
        return String(letters).uppercased()
    }

    /// Decoding is lenient about `role`: an unrecognised value maps to `.damage`
    /// rather than failing the whole catalog fetch when Blizzard adds a category.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        name = try c.decode(String.self, forKey: .name)
        portrait = try c.decodeIfPresent(URL.self, forKey: .portrait)
        role = (try? c.decode(Role.self, forKey: .role)) ?? .damage
        subrole = try c.decodeIfPresent(String.self, forKey: .subrole)
        gamemodes = try c.decodeIfPresent([String].self, forKey: .gamemodes)
    }
}
