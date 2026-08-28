import Foundation

/// What the watch listens to: the phone while it's in range, the PC when it isn't.
///
/// The relay is the cheap path — WatchConnectivity coalesces, is delivered even while the
/// watch app is asleep, and costs the watch no radio of its own — so it stays the default.
/// But it goes quiet the moment the phone is out of range, switched off or flat, and that
/// used to be the end of the queue on your wrist.
///
/// Since the phone hands over its pairing the moment it scans a code, the watch can open
/// its own socket to the PC instead, over the same Wi-Fi, with nothing to set up. That is
/// the whole reason the pairing travels: one scan configures both devices.
///
/// Snapshots from either source land in the same store, and it drops anything that isn't
/// strictly newer — so a handover mid-queue can't rewind the screen, however the two
/// sources happen to interleave.
@MainActor
public final class WatchTransport: QueueTransport {
    public var name: String { isDirect ? "PC" : "iPhone" }

    public private(set) var status: TransportStatus = .offline {
        didSet { if status != oldValue { onStatusChange?(status) } }
    }

    public var onEvent: ((QueueEvent) -> Void)?
    public var onStatusChange: ((TransportStatus) -> Void)?

    /// Which PC the phone told us about. Nil until it has paired with one.
    public private(set) var pairing: Pairing?

    private let relay: any QueueTransport
    private let makeDirect: (Pairing) -> any QueueTransport
    private var direct: (any QueueTransport)?
    private var isDirect = false
    private var wantsConnection = false

    /// `relay` and `makeDirect` are seams for the tests: the real ones need a paired
    /// watch and a PC on the network, and the switching between them is the part worth
    /// being sure about.
    public init(pairing: Pairing?,
                identity: ClientIdentity,
                relay: (any QueueTransport)? = nil,
                makeDirect: ((Pairing) -> any QueueTransport)? = nil) {
        self.pairing = pairing
        self.relay = relay ?? WatchRelayTransport()
        self.makeDirect = makeDirect ?? { pairing in
            WebSocketTransport(pairing: pairing, identity: identity)
        }
    }

    // MARK: - QueueTransport

    public func connect() {
        wantsConnection = true
        relay.onEvent = { [weak self] event in self?.receive(event) }
        relay.onStatusChange = { [weak self] status in self?.relayChanged(to: status) }
        relay.connect()
    }

    public func disconnect() {
        wantsConnection = false
        relay.onEvent = nil
        relay.onStatusChange = nil
        relay.disconnect()
        stopDirect()
        status = .offline
    }

    public func send(_ command: ClientCommand) {
        // Whichever side we're actually listening to is the side that can act on it.
        if isDirect {
            direct?.send(command)
        } else {
            relay.send(command)
        }
    }

    // MARK: - The handover

    /// The phone paired, re-paired, or unpaired.
    public func adopt(_ pairing: Pairing?) {
        guard pairing != self.pairing else { return }
        self.pairing = pairing

        // A socket open to the old PC is no longer one we're allowed to be on.
        stopDirect()
        if wantsConnection, !relayIsHealthy {
            startDirectIfPossible()
        }
    }

    private var relayIsHealthy: Bool {
        if case .connected = relay.status { return true }
        if case .connecting = relay.status { return true }
        return false
    }

    private func relayChanged(to relayStatus: TransportStatus) {
        switch relayStatus {
        case .connected, .connecting:
            // The phone is back. It is the cheaper source, so hand back to it.
            stopDirect()
            status = relayStatus
        case .offline, .failed:
            if startDirectIfPossible() { return }
            status = relayStatus
        }
    }

    @discardableResult
    private func startDirectIfPossible() -> Bool {
        guard wantsConnection, !isDirect, let pairing else { return false }

        let socket = makeDirect(pairing)
        socket.onEvent = { [weak self] event in self?.receive(event) }
        socket.onStatusChange = { [weak self] status in
            guard let self, self.isDirect else { return }
            self.status = status
        }
        direct = socket
        isDirect = true
        socket.connect()
        return true
    }

    private func stopDirect() {
        guard let socket = direct else { return }
        socket.onEvent = nil
        socket.onStatusChange = nil
        socket.disconnect()
        direct = nil
        isDirect = false
    }

    private func receive(_ event: QueueEvent) {
        onEvent?(event)
    }
}
