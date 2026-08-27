import Foundation
import Observation
import WatchKit

/// Watch-side counterpart to `AppModel`. State arrives from the paired iPhone; votes and
/// hero picks go back the same way.
@MainActor
@Observable
public final class WatchModel {
    public let store = QueueStore()
    public let catalog = CatalogService.shared
    public private(set) var matchFoundToken = 0

    private let relay = WatchRelayTransport()

    public init() {
        store.onPhaseChange = { [weak self] _, next in
            guard let self else { return }
            switch next.kind {
            case .matchFound:
                self.matchFoundToken += 1
                // The wrist tap is the whole reason the watch app exists — this is the
                // signal that reaches you when the phone is in a pocket.
                WKInterfaceDevice.current().play(.notification)
            case .mapVote, .heroSelect:
                WKInterfaceDevice.current().play(.directionUp)
                // Same reasoning as on the phone, and more acute here: the watch has a
                // slower link, so art must be in hand before the grid appears.
                switch next {
                case .mapVote(let info):
                    ImageCache.shared.prefetch(info.options.map { self.catalog.map($0.mapKey)?.screenshot })
                case .heroSelect:
                    ImageCache.shared.prefetch(self.store.selectableHeroes().map(\.portrait))
                default:
                    break
                }
            default:
                break
            }
        }
        store.onSnapshot = { snapshot in
            SharedState.write(snapshot)
        }
    }

    public func start() {
        #if DEBUG
        // Standalone mode: `-demoPhase <kind>` drives the watch from its own mock instead
        // of the paired iPhone. WatchConnectivity between paired *simulators* is
        // unreliable, so without this the watch UI would only be testable on hardware.
        let args = ProcessInfo.processInfo.arguments
        if let flag = args.firstIndex(of: "-demoPhase"), args.count > flag + 1,
           let kind = QueuePhase.Kind(rawValue: args[flag + 1]) {
            store.use(standalone)
            Task {
                await catalog.load()
                applyDemoPhase(kind)
            }
            return
        }
        #endif
        store.use(relay)
        Task { await catalog.load() }
    }

    #if DEBUG
    private let standalone = MockTransport()

    private func applyDemoPhase(_ kind: QueuePhase.Kind) {
        let mode = QueueMode.competitive
        let role = Role.tank
        let searching = QueuePhase.searching(
            SearchInfo(mode: mode, role: role,
                       startedAt: .now.addingTimeInterval(-137), estimatedWait: 240))
        let found = QueuePhase.matchFound(
            MatchFoundInfo(mode: mode, role: role, waited: 137, lockInAt: .now + 15))

        switch kind {
        case .idle:
            break
        case .searching:
            standalone.apply(searching)
        case .matchFound:
            standalone.apply(searching); standalone.apply(found)
        case .mapVote:
            standalone.apply(searching); standalone.apply(found)
            standalone.apply(.mapVote(MapVoteInfo(options: catalog.mapVoteOptions(for: mode),
                                                  deadline: .now + 25)))
        case .heroSelect:
            standalone.apply(searching); standalone.apply(found)
            standalone.apply(.heroSelect(HeroSelectInfo(mode: mode, role: role,
                                                        mapKey: catalog.maps(for: mode).randomElement()?.key,
                                                        deadline: .now + 40)))
        case .inGame:
            standalone.apply(searching); standalone.apply(found)
            standalone.apply(.inGame(InGameInfo(mode: mode,
                                                mapKey: catalog.maps(for: mode).randomElement()?.key,
                                                heroKey: catalog.heroes(role: role, mode: mode).randomElement()?.key,
                                                startedAt: .now.addingTimeInterval(-95))))
        case .cancelled:
            standalone.apply(.cancelled(CancelInfo(reason: .matchCancelled)))
        }
    }
    #endif
}
