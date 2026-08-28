import XCTest

/// The watch's handover between the phone and the PC. The real pair needs a watch on a
/// wrist and a server on the network, so both sides are stood in for here — what is
/// being tested is the switching, which is the part with states to get wrong.
@MainActor
final class WatchTransportTests: XCTestCase {

    private let pc = Pairing(host: "192.168.1.14", port: 8787, token: "token-a")
    private let otherPC = Pairing(host: "192.168.1.99", port: 8787, token: "token-b")
    private let identity = ClientIdentity(kind: .watch, name: "Watch", appVersion: "1.0")

    /// A transport that reports whatever it's told to.
    private final class FakeTransport: QueueTransport {
        let name: String
        var status: TransportStatus = .offline
        var onEvent: ((QueueEvent) -> Void)?
        var onStatusChange: ((TransportStatus) -> Void)?

        private(set) var isConnected = false
        private(set) var sent: [ClientCommand] = []

        init(name: String) { self.name = name }

        func connect() { isConnected = true }
        func disconnect() { isConnected = false }
        func send(_ command: ClientCommand) { sent.append(command) }

        func report(_ status: TransportStatus) {
            self.status = status
            onStatusChange?(status)
        }

        func deliver(_ phase: QueuePhase, sequence: Int = 1) {
            onEvent?(.snapshot(QueueSnapshot(sequence: sequence, phase: phase)))
        }
    }

    private func makeTransport(pairing: Pairing?)
        -> (WatchTransport, FakeTransport, () -> [FakeTransport]) {
        let relay = FakeTransport(name: "iPhone")
        var sockets: [FakeTransport] = []
        let transport = WatchTransport(pairing: pairing, identity: identity, relay: relay) { _ in
            let socket = FakeTransport(name: "PC")
            sockets.append(socket)
            return socket
        }
        return (transport, relay, { sockets })
    }

    // MARK: - The default path

    func testListensToThePhoneWhileItIsThere() {
        let (transport, relay, sockets) = makeTransport(pairing: pc)
        transport.connect()
        relay.report(.connected)

        XCTAssertEqual(transport.name, "iPhone")
        XCTAssertTrue(sockets().isEmpty, "the PC socket is a fallback, not the default")
        XCTAssertEqual(transport.status, .connected)
    }

    func testSnapshotsFromThePhoneAreForwarded() {
        let (transport, relay, _) = makeTransport(pairing: pc)
        var seen: [QueuePhase] = []
        transport.onEvent = { if case .snapshot(let s) = $0 { seen.append(s.phase) } }
        transport.connect()
        relay.report(.connected)
        relay.deliver(.idle)

        XCTAssertEqual(seen.count, 1)
    }

    // MARK: - The handover

    func testFallsBackToThePCWhenThePhoneGoesAway() {
        let (transport, relay, sockets) = makeTransport(pairing: pc)
        transport.connect()
        relay.report(.connected)
        relay.report(.failed("iPhone not reachable"))

        XCTAssertEqual(sockets().count, 1, "should have opened its own socket")
        XCTAssertTrue(sockets()[0].isConnected)
        XCTAssertEqual(transport.name, "PC")
    }

    func testSnapshotsFromThePCAreForwardedToo() {
        let (transport, relay, sockets) = makeTransport(pairing: pc)
        var seen: [QueuePhase] = []
        transport.onEvent = { if case .snapshot(let s) = $0 { seen.append(s.phase) } }
        transport.connect()
        relay.report(.failed("iPhone not reachable"))
        sockets()[0].deliver(.idle)

        XCTAssertEqual(seen.count, 1)
    }

    func testHandsBackToThePhoneWhenItReturns() {
        let (transport, relay, sockets) = makeTransport(pairing: pc)
        transport.connect()
        relay.report(.failed("iPhone not reachable"))
        XCTAssertTrue(sockets()[0].isConnected)

        relay.report(.connected)
        XCTAssertFalse(sockets()[0].isConnected, "the phone is cheaper; stop the radio")
        XCTAssertEqual(transport.name, "iPhone")
        XCTAssertEqual(transport.status, .connected)
    }

    func testWithoutAPairingItReportsTheFailureRatherThanHanging() {
        let (transport, relay, sockets) = makeTransport(pairing: nil)
        transport.connect()
        relay.report(.failed("iPhone not reachable"))

        XCTAssertTrue(sockets().isEmpty)
        XCTAssertEqual(transport.status, .failed("iPhone not reachable"))
    }

    // MARK: - Pairing arriving from the phone

    func testAPairingArrivingWhileOfflineOpensTheSocket() {
        let (transport, relay, sockets) = makeTransport(pairing: nil)
        transport.connect()
        relay.report(.failed("iPhone not reachable"))
        XCTAssertTrue(sockets().isEmpty)

        transport.adopt(pc)                       // the phone scanned a code
        XCTAssertEqual(sockets().count, 1)
        XCTAssertTrue(sockets()[0].isConnected)
    }

    func testAPairingArrivingWhileThePhoneIsThereDoesNotStartARadio() {
        let (transport, relay, sockets) = makeTransport(pairing: nil)
        transport.connect()
        relay.report(.connected)

        transport.adopt(pc)
        XCTAssertTrue(sockets().isEmpty, "no reason to open a socket while the phone is here")
        XCTAssertEqual(transport.name, "iPhone")
    }

    func testRepairingToAnotherPCLeavesTheOldSocket() {
        let (transport, relay, sockets) = makeTransport(pairing: pc)
        transport.connect()
        relay.report(.failed("iPhone not reachable"))

        transport.adopt(otherPC)
        XCTAssertEqual(sockets().count, 2, "a second socket, to the new PC")
        XCTAssertFalse(sockets()[0].isConnected, "the old PC is not ours any more")
        XCTAssertTrue(sockets()[1].isConnected)
    }

    func testUnpairingClosesTheSocket() {
        let (transport, relay, sockets) = makeTransport(pairing: pc)
        transport.connect()
        relay.report(.failed("iPhone not reachable"))
        XCTAssertTrue(sockets()[0].isConnected)

        transport.adopt(nil)
        XCTAssertFalse(sockets()[0].isConnected)
        XCTAssertNil(transport.pairing)
    }

    func testTheSamePairingArrivingAgainChangesNothing() {
        // The phone re-sends on every launch, and that must not churn the connection.
        let (transport, relay, sockets) = makeTransport(pairing: pc)
        transport.connect()
        relay.report(.failed("iPhone not reachable"))

        transport.adopt(pc)
        XCTAssertEqual(sockets().count, 1, "should not have reconnected")
        XCTAssertTrue(sockets()[0].isConnected)
    }

    // MARK: - Commands

    func testCommandsGoToWhicheverSideIsListening() {
        let (transport, relay, sockets) = makeTransport(pairing: pc)
        transport.connect()
        relay.report(.connected)
        transport.send(.voteMap(mapKey: "ilios"))
        XCTAssertEqual(relay.sent.count, 1)

        relay.report(.failed("iPhone not reachable"))
        transport.send(.selectHero(heroKey: "ana"))
        XCTAssertEqual(relay.sent.count, 1, "the phone can't act on it now")
        XCTAssertEqual(sockets()[0].sent.count, 1)
    }

    // MARK: - Relay staleness (bug: phone locks, WC stays reachable, data goes silent)

    /// `WatchLink.shared` is a singleton backed by real `WatchConnectivity`, so this drives
    /// `WatchRelayTransport` through the same closures `WatchLink` would call, rather than
    /// standing up a fake `WatchLink`. `staleTimeout`/`pollInterval` are cut way down so the
    /// test doesn't wait 25 real seconds.
    func testReachabilityAloneDoesNotKeepAStalledRelayConnectedForever() async {
        let relay = WatchRelayTransport(staleTimeout: 0.05, pollInterval: 0.02)
        var statuses: [TransportStatus] = []
        relay.onStatusChange = { statuses.append($0) }
        relay.connect()

        WatchLink.shared.onReachabilityChange?(true)
        WatchLink.shared.onSnapshot?(QueueSnapshot(sequence: 1, phase: .idle))
        XCTAssertEqual(relay.status, .connected)

        // No further snapshots arrive — as if the phone's own socket to the PC died while
        // WatchConnectivity reachability stayed true (the exact gap that let the watch sit
        // at "connected" showing nothing).
        try? await Task.sleep(for: .seconds(0.2))

        XCTAssertNotEqual(relay.status, .connected,
                          "reachability without fresh data must not read as connected")
        relay.disconnect()
    }

    func testAFreshSnapshotResetsTheStaleClock() async {
        let relay = WatchRelayTransport(staleTimeout: 0.05, pollInterval: 0.02)
        relay.onStatusChange = { _ in }
        relay.connect()

        WatchLink.shared.onReachabilityChange?(true)
        for _ in 0..<4 {
            WatchLink.shared.onSnapshot?(QueueSnapshot(sequence: 1, phase: .idle))
            try? await Task.sleep(for: .seconds(0.03))
        }

        XCTAssertEqual(relay.status, .connected, "snapshots kept arriving faster than the timeout")
        relay.disconnect()
    }

    func testStaleRelayTriggersTheDirectFailover() async {
        let relay = WatchRelayTransport(staleTimeout: 0.05, pollInterval: 0.02)
        var sockets: [FakeTransport] = []
        let transport = WatchTransport(pairing: pc, identity: identity, relay: relay) { _ in
            let socket = FakeTransport(name: "PC")
            sockets.append(socket)
            return socket
        }
        transport.connect()

        WatchLink.shared.onReachabilityChange?(true)
        WatchLink.shared.onSnapshot?(QueueSnapshot(sequence: 1, phase: .idle))
        XCTAssertTrue(sockets.isEmpty, "should still be on the relay while it's fresh")

        try? await Task.sleep(for: .seconds(0.2))

        XCTAssertEqual(sockets.count, 1, "a stale-but-reachable relay should fail over just like an unreachable one")
        XCTAssertTrue(sockets[0].isConnected)
        transport.disconnect()
    }

    func testDisconnectingStopsBothSides() {
        let (transport, relay, sockets) = makeTransport(pairing: pc)
        transport.connect()
        relay.report(.failed("iPhone not reachable"))

        transport.disconnect()
        XCTAssertFalse(relay.isConnected)
        XCTAssertFalse(sockets()[0].isConnected)
        XCTAssertEqual(transport.status, .offline)
    }
}
