import Foundation
import Observation

/// Hero and map data, fetched from the OverFast API rather than baked into the app.
///
/// Three layers, each a fallback for the one before:
/// 1. Network fetch — always current, so heroes and maps added to Overwatch appear
///    without shipping a new build.
/// 2. On-disk cache in Application Support — survives launches, works offline.
/// 3. A JSON snapshot in the bundle — covers a first launch with no network.
///
/// Portraits are *referenced*, never copied: `Hero.portrait` is a Blizzard CDN URL that
/// `AsyncImage` loads through a shared `URLCache`.
@MainActor
@Observable
public final class CatalogService {
    public private(set) var heroes: [Hero] = []
    public private(set) var maps: [OverwatchMap] = []
    public private(set) var isLoading = false
    public private(set) var lastError: String?
    public private(set) var source: Source = .none

    public enum Source: String, Sendable {
        case none, bundle, cache, network

        public var displayText: String {
            switch self {
            case .none: return "Not loaded"
            case .bundle: return "Bundled snapshot"
            case .cache: return "Cached"
            case .network: return "Live from OverFast"
            }
        }
    }

    public static let shared = CatalogService()

    private static let heroesURL = URL(string: "https://overfast-api.tekrop.fr/heroes?locale=en-us")!
    private static let mapsURL = URL(string: "https://overfast-api.tekrop.fr/maps")!

    /// Refetch at most this often; the catalog changes a few times a year.
    private static let refreshInterval: TimeInterval = 60 * 60 * 24

    private struct Payload: Codable {
        var heroes: [Hero]
        var maps: [OverwatchMap]
        var fetchedAt: Date
    }

    private let session: URLSession

    public init(session: URLSession? = nil) {
        if let session {
            self.session = session
        } else {
            let config = URLSessionConfiguration.default
            // Generous on-disk cache: portraits are the bulk of it and never change
            // for a given URL (the filename is a content hash).
            config.urlCache = URLCache(memoryCapacity: 16 * 1024 * 1024,
                                       diskCapacity: 256 * 1024 * 1024,
                                       diskPath: "overwatch-catalog")
            config.requestCachePolicy = .returnCacheDataElseLoad
            self.session = URLSession(configuration: config)
        }
    }

    // MARK: - Loading

    /// Fills `heroes`/`maps` from the fastest source available, then refreshes from the
    /// network in the background if the cache is stale. Safe to call on every launch.
    public func load() async {
        if heroes.isEmpty {
            if let cached = readCache() {
                apply(cached, source: .cache)
                if Date.now.timeIntervalSince(cached.fetchedAt) < Self.refreshInterval {
                    return
                }
            } else if let bundled = readBundle() {
                apply(bundled, source: .bundle)
            }
        }
        await refresh()
    }

    /// Forces a network fetch. Failure is non-fatal: whatever was already loaded stays.
    public func refresh() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }

        do {
            async let heroData = fetch(Self.heroesURL)
            async let mapData = fetch(Self.mapsURL)
            let (h, m) = try await (heroData, mapData)

            let decoded = Payload(heroes: try Wire.decoder.decode([Hero].self, from: h),
                                  maps: try Wire.decoder.decode([OverwatchMap].self, from: m),
                                  fetchedAt: .now)
            guard !decoded.heroes.isEmpty, !decoded.maps.isEmpty else {
                lastError = "Catalog came back empty"
                return
            }
            apply(decoded, source: .network)
            writeCache(decoded)
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func fetch(_ url: URL) async throws -> Data {
        var request = URLRequest(url: url)
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw URLError(.badServerResponse)
        }
        return data
    }

    private func apply(_ payload: Payload, source: Source) {
        heroes = payload.heroes.sorted { $0.name < $1.name }
        maps = payload.maps.sorted { $0.name < $1.name }
        self.source = source
    }

    // MARK: - Lookup

    public func hero(_ key: String?) -> Hero? {
        guard let key else { return nil }
        return heroes.first { $0.key == key }
    }

    public func map(_ key: String?) -> OverwatchMap? {
        guard let key else { return nil }
        return maps.first { $0.key == key }
    }

    public func heroes(role: Role, mode: QueueMode) -> [Hero] {
        heroes.filter { $0.isAvailable(in: mode) && (role == .flex || role == .open || $0.role == role) }
    }

    /// Maps a given queue could actually put you on — this is what the vote draws from.
    public func maps(for mode: QueueMode) -> [OverwatchMap] {
        let pool = MapType.pool(for: mode)
        return maps.filter { !$0.gamemodes.isEmpty && !pool.isDisjoint(with: Set($0.gamemodes)) }
    }

    /// Three distinct maps for a vote, biased to distinct objective types so the choice
    /// isn't "three Escort maps".
    public func mapVoteOptions(for mode: QueueMode, count: Int = 3) -> [MapOption] {
        let pool = maps(for: mode).shuffled()
        var chosen: [OverwatchMap] = []
        var usedTypes: Set<MapType> = []
        for map in pool where !usedTypes.contains(map.primaryType) {
            chosen.append(map)
            usedTypes.insert(map.primaryType)
            if chosen.count == count { break }
        }
        for map in pool where chosen.count < count {
            if !chosen.contains(map) { chosen.append(map) }
        }
        return chosen.map { MapOption(mapKey: $0.key) }
    }

    // MARK: - Persistence

    private var cacheURL: URL? {
        guard let dir = try? FileManager.default.url(for: .applicationSupportDirectory,
                                                     in: .userDomainMask,
                                                     appropriateFor: nil, create: true) else { return nil }
        return dir.appendingPathComponent("overwatch-catalog.json")
    }

    private func readCache() -> Payload? {
        guard let url = cacheURL, let data = try? Data(contentsOf: url) else { return nil }
        return try? Wire.decoder.decode(Payload.self, from: data)
    }

    private func writeCache(_ payload: Payload) {
        guard let url = cacheURL, let data = try? Wire.encoder.encode(payload) else { return }
        try? data.write(to: url, options: .atomic)
    }

    private func readBundle() -> Payload? {
        guard let url = Bundle.main.url(forResource: "catalog-fallback", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? Wire.decoder.decode(Payload.self, from: data)
    }
}
