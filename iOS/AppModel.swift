import Foundation
import Observation
import SwiftUI

/// Wires the store to a transport and to the platform features that react to phase
/// changes: the Live Activity, haptics, sound, and the relay down to the watch.
@MainActor
@Observable
public final class AppModel {
    public let store = QueueStore()
    public let catalog = CatalogService.shared

    /// Which state source is in use. Persisted so a relaunch comes back the same way.
    public enum Source: String, CaseIterable, Identifiable, Sendable {
        case mock, server
        public var id: String { rawValue }
        public var displayName: String { self == .mock ? "Mock" : "PC Server" }
    }

    public var source: Source {
        didSet {
            guard source != oldValue else { return }
            UserDefaults.standard.set(source.rawValue, forKey: Keys.source)
            connect()
        }
    }

    public var endpoint: WebSocketTransport.Endpoint {
        didSet {
            guard endpoint != oldValue else { return }
            if let data = try? Wire.encoder.encode(endpoint) {
                UserDefaults.standard.set(data, forKey: Keys.endpoint)
            }
            if source == .server { connect() }
        }
    }

    /// The mock driver, kept alive across transport swaps so the debug panel can always
    /// reach it.
    public let mock = MockTransport()

    /// Bumped every time a match lands. Views observe it to fire one-shot animations
    /// without having to diff the phase themselves.
    public private(set) var matchFoundToken = 0

    private enum Keys {
        static let source = "transport.source"
        static let endpoint = "transport.endpoint"
    }

    public init() {
        let raw = UserDefaults.standard.string(forKey: Keys.source) ?? Source.mock.rawValue
        source = Source(rawValue: raw) ?? .mock

        if let data = UserDefaults.standard.data(forKey: Keys.endpoint),
           let saved = try? Wire.decoder.decode(WebSocketTransport.Endpoint.self, from: data) {
            endpoint = saved
        } else {
            endpoint = .init()
        }

        store.onPhaseChange = { [weak self] previous, next in
            self?.react(from: previous, to: next)
        }
        store.onSnapshot = { [weak self] snapshot in
            guard let self else { return }
            // The watch mirrors whatever the phone is showing, mock or real.
            WatchLink.shared.send(snapshot: snapshot)
            // The activity gets the store's drift correction too, so its timers and its
            // wait bar run from the same origin as the ones on screen.
            LiveActivityController.shared.sync(to: snapshot, clock: store.clock)
            SharedState.write(snapshot)
        }
    }

    public func start() {
        // Before anything can push: take over the activity a previous launch left on the
        // Lock Screen, rather than stacking a new one on top of it.
        LiveActivityController.shared.adoptRunningActivity()
        WatchLink.shared.activate()
        WatchLink.shared.onCommand = { [weak self] command in
            // A vote or hero pick made on the wrist is handled exactly as if it had been
            // tapped on the phone, so both surfaces stay in agreement.
            self?.store.transport?.send(command)
            if case .voteMap(let key) = command { self?.store.vote(map: key) }
            if case .selectHero(let key) = command { self?.store.select(hero: key) }
        }
        connect()
        Task {
            await catalog.load()
            applyLaunchPhaseIfRequested()
        }
    }

    /// Jumps straight to a phase from a launch argument, e.g.
    /// `xcrun simctl launch <device> <bundle-id> -demoPhase mapVote`.
    ///
    /// Exists so a phase can be put on screen deterministically — for screenshots, for
    /// checking a layout without playing through a queue, and for demoing the app
    /// without a server. Waits for the catalog so map and hero phases have real content.
    private func applyLaunchPhaseIfRequested() {
        #if DEBUG
        let args = ProcessInfo.processInfo.arguments
        guard let flag = args.firstIndex(of: "-demoPhase"), args.count > flag + 1,
              let kind = QueuePhase.Kind(rawValue: args[flag + 1]) else { return }

        source = .mock
        mock.reset()
        let mode = QueueMode.competitive
        let role = Role.tank

        func searching(_ ago: TimeInterval) -> QueuePhase {
            .searching(SearchInfo(mode: mode, role: role,
                                  startedAt: .now.addingTimeInterval(-ago), estimatedWait: 240))
        }

        // Each jump replays the legal path to that phase, so the state machine is
        // exercised rather than bypassed.
        switch kind {
        case .idle:
            break
        case .searching:
            mock.apply(searching(137))
        case .matchFound:
            mock.apply(searching(137))
            mock.apply(.matchFound(MatchFoundInfo(mode: mode, role: role, waited: 137,
                                                  lockInAt: .now + 15)))
        case .mapVote:
            mock.apply(searching(137))
            mock.apply(.matchFound(MatchFoundInfo(mode: mode, role: role, waited: 137,
                                                  lockInAt: .now + 15)))
            mock.apply(.mapVote(MapVoteInfo(options: catalog.mapVoteOptions(for: mode),
                                            deadline: .now + 25)))
        case .heroSelect:
            mock.apply(searching(137))
            mock.apply(.matchFound(MatchFoundInfo(mode: mode, role: role, waited: 137,
                                                  lockInAt: .now + 15)))
            mock.apply(.heroSelect(HeroSelectInfo(mode: mode, role: role,
                                                  mapKey: catalog.maps(for: mode).randomElement()?.key,
                                                  deadline: .now + 40)))
        case .inGame:
            mock.apply(searching(137))
            mock.apply(.matchFound(MatchFoundInfo(mode: mode, role: role, waited: 137,
                                                  lockInAt: .now + 15)))
            mock.apply(.inGame(InGameInfo(mode: mode,
                                          mapKey: catalog.maps(for: mode).randomElement()?.key,
                                          heroKey: catalog.heroes(role: role, mode: mode).randomElement()?.key,
                                          startedAt: .now.addingTimeInterval(-95))))
        case .cancelled:
            mock.apply(.cancelled(CancelInfo(reason: .matchCancelled)))
        }
        #endif
    }

    public func connect() {
        switch source {
        case .mock:
            store.use(mock)
        case .server:
            let identity = ClientIdentity(kind: .phone,
                                          name: UIDevice.current.name,
                                          appVersion: Bundle.main.appVersion)
            store.use(WebSocketTransport(endpoint: endpoint, identity: identity))
        }
    }

    /// Pulls the art for a phase into the cache the moment that phase starts, so tiles
    /// are already decoded by the time they animate in — the vote and hero-select windows
    /// are short, and a grid that pops in one portrait at a time wastes them.
    private func prefetchArt(for phase: QueuePhase) {
        switch phase {
        case .mapVote(let info):
            ImageCache.shared.prefetch(info.options.map { catalog.map($0.mapKey)?.screenshot })
        case .heroSelect:
            ImageCache.shared.prefetch(store.selectableHeroes().map(\.portrait))
        default:
            break
        }
    }

    private func react(from previous: QueuePhase, to next: QueuePhase) {
        switch next.kind {
        case .matchFound:
            matchFoundToken += 1
            Haptics.matchFound()
            SoundPlayer.shared.playMatchFound()
        case .mapVote, .heroSelect:
            Haptics.attention()
            prefetchArt(for: next)
        case .idle, .cancelled:
            LiveActivityController.shared.end()
        default:
            break
        }
    }
}

public extension Bundle {
    var appVersion: String {
        (infoDictionary?["CFBundleShortVersionString"] as? String) ?? "1.0"
    }
}
