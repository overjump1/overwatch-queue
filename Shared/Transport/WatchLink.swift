import Foundation
#if canImport(WatchConnectivity)
import WatchConnectivity
#endif

/// The iPhone ⇄ Apple Watch pipe. The phone owns the server connection and pushes each
/// snapshot down; the watch pushes the player's votes and hero picks back up.
///
/// Two delivery methods, on purpose:
/// - `updateApplicationContext` for state. It coalesces (only the latest matters) and is
///   delivered even if the counterpart app is asleep, which is the normal case for the watch.
/// - `sendMessage` for immediacy when the counterpart is reachable, because during a match
///   -found window a few seconds of latency is the whole point of the feature.
@MainActor
public final class WatchLink: NSObject {
    public static let shared = WatchLink()

    public var onSnapshot: ((QueueSnapshot) -> Void)?
    public var onCommand: ((ClientCommand) -> Void)?
    public var onReachabilityChange: ((Bool) -> Void)?
    /// The phone handing over which PC it paired with — nil when it has unpaired.
    public var onPairing: ((Pairing?) -> Void)?

    private enum Key {
        static let snapshot = "snapshot"
        static let command = "command"
        static let pairing = "pairing"
        static let unpaired = "unpaired"
    }

    /// The application context is replaced wholesale on every write, so the two things
    /// the phone sends have to be written together or each would erase the other.
    private var latestSnapshot: Data?
    private var latestPairing: Data?
    private var hasUnpaired = false

    public private(set) var isSupported = false
    public private(set) var isReachable = false {
        didSet { if isReachable != oldValue { onReachabilityChange?(isReachable) } }
    }

    private override init() {
        super.init()
    }

    public func activate() {
        #if canImport(WatchConnectivity)
        guard WCSession.isSupported() else {
            isSupported = false
            return
        }
        isSupported = true
        let session = WCSession.default
        session.delegate = self
        session.activate()
        #endif
    }

    // MARK: - Sending

    /// iPhone → Watch.
    public func send(snapshot: QueueSnapshot) {
        #if canImport(WatchConnectivity)
        guard isSupported, WCSession.default.activationState == .activated else { return }
        guard let data = try? Wire.encode(snapshot) else { return }
        latestSnapshot = data
        pushContext()

        if WCSession.default.isReachable {
            WCSession.default.sendMessage([Key.snapshot: data], replyHandler: nil, errorHandler: nil)
        }
        #endif
    }

    /// iPhone → Watch: which PC to talk to, and the token to prove it may.
    ///
    /// Sent the moment the phone scans a code, so the watch is configured by the same
    /// one scan and there is nothing to set up on the wrist. It rides the application
    /// context because that is delivered even when the watch app is asleep, which is
    /// where a watch app spends nearly all of its time.
    public func send(pairing: Pairing?) {
        #if canImport(WatchConnectivity)
        guard isSupported, WCSession.default.activationState == .activated else { return }

        if let pairing, let data = try? Wire.encoder.encode(pairing) {
            latestPairing = data
            hasUnpaired = false
        } else {
            latestPairing = nil
            hasUnpaired = true
        }
        pushContext()

        if WCSession.default.isReachable {
            WCSession.default.sendMessage(pairingPayload(), replyHandler: nil, errorHandler: nil)
        }
        #endif
    }

    #if canImport(WatchConnectivity)
    private func pairingPayload() -> [String: Any] {
        if let latestPairing { return [Key.pairing: latestPairing] }
        return [Key.unpaired: true]
    }

    private func pushContext() {
        var context: [String: Any] = [:]
        if let latestSnapshot { context[Key.snapshot] = latestSnapshot }
        if let latestPairing {
            context[Key.pairing] = latestPairing
        } else if hasUnpaired {
            context[Key.unpaired] = true
        }
        guard !context.isEmpty else { return }
        // Throws only if the payload is invalid, which it isn't.
        try? WCSession.default.updateApplicationContext(context)
    }
    #endif

    /// Watch → iPhone.
    public func send(command: ClientCommand) {
        #if canImport(WatchConnectivity)
        guard isSupported, WCSession.default.activationState == .activated else { return }
        guard let data = try? Wire.encode(command) else { return }

        if WCSession.default.isReachable {
            WCSession.default.sendMessage([Key.command: data], replyHandler: nil, errorHandler: { _ in
                // Reachability can lapse between the check and the send; fall back to the
                // guaranteed-delivery queue so a hero pick is never silently lost.
                WCSession.default.transferUserInfo([Key.command: data])
            })
        } else {
            WCSession.default.transferUserInfo([Key.command: data])
        }
        #endif
    }

    // MARK: - Receiving

    private func ingest(_ payload: [String: Any]) {
        if let data = payload[Key.snapshot] as? Data,
           let snapshot = try? Wire.decode(QueueSnapshot.self, from: data) {
            onSnapshot?(snapshot)
        }
        if let data = payload[Key.command] as? Data,
           let command = try? Wire.decode(ClientCommand.self, from: data) {
            onCommand?(command)
        }
        if let data = payload[Key.pairing] as? Data,
           let pairing = try? Wire.decoder.decode(Pairing.self, from: data) {
            onPairing?(pairing)
        } else if payload[Key.unpaired] as? Bool == true {
            onPairing?(nil)
        }
    }
}

#if canImport(WatchConnectivity)
extension WatchLink: WCSessionDelegate {
    nonisolated public func session(_ session: WCSession,
                                    activationDidCompleteWith state: WCSessionActivationState,
                                    error: Error?) {
        let reachable = session.isReachable
        Task { @MainActor in self.isReachable = reachable }
    }

    nonisolated public func sessionReachabilityDidChange(_ session: WCSession) {
        let reachable = session.isReachable
        Task { @MainActor in self.isReachable = reachable }
    }

    nonisolated public func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        Task { @MainActor in self.ingest(message) }
    }

    nonisolated public func session(_ session: WCSession, didReceiveApplicationContext context: [String: Any]) {
        Task { @MainActor in self.ingest(context) }
    }

    nonisolated public func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any]) {
        Task { @MainActor in self.ingest(userInfo) }
    }

    #if os(iOS)
    nonisolated public func sessionDidBecomeInactive(_ session: WCSession) {}

    nonisolated public func sessionDidDeactivate(_ session: WCSession) {
        // Reactivate so the link survives the user switching paired watches.
        WCSession.default.activate()
    }
    #endif
}
#endif

/// The watch's `QueueTransport`: state arrives from the paired iPhone, commands go back
/// the same way. Swapping this for `WebSocketTransport` is all it takes to make the watch
/// talk to the PC directly.
@MainActor
public final class WatchRelayTransport: QueueTransport {
    public let name = "iPhone"
    public private(set) var status: TransportStatus = .offline {
        didSet { if status != oldValue { onStatusChange?(status) } }
    }

    public var onEvent: ((QueueEvent) -> Void)?
    public var onStatusChange: ((TransportStatus) -> Void)?

    private let link = WatchLink.shared
    private var lastSnapshotAt: Date = .distantPast
    private var staleWatchdog: Task<Void, Never>?

    /// How long a reachable-but-silent phone gets before its data counts as stale, and how
    /// often that's checked. Injectable so tests don't have to wait 25 real seconds; the
    /// defaults are comfortably longer than the phone's own socket heartbeat, so a
    /// genuinely live phone never trips this.
    private let staleTimeout: TimeInterval
    private let pollInterval: TimeInterval

    public convenience init() {
        self.init(staleTimeout: 25, pollInterval: 5)
    }

    init(staleTimeout: TimeInterval, pollInterval: TimeInterval) {
        self.staleTimeout = staleTimeout
        self.pollInterval = pollInterval
    }

    public func connect() {
        status = .connecting
        lastSnapshotAt = .distantPast
        link.onSnapshot = { [weak self] snapshot in
            guard let self else { return }
            self.lastSnapshotAt = .now
            self.status = .connected
            self.onEvent?(.snapshot(snapshot))
        }
        link.onReachabilityChange = { [weak self] reachable in
            guard let self else { return }
            // A snapshot already received stays valid while the phone is out of range —
            // the queue kept going, we just can't see updates. Say that rather than
            // pretending to be connected.
            self.status = reachable ? .connected : .failed("iPhone not reachable")
        }
        link.activate()
        if !link.isSupported {
            status = .failed("Watch connectivity unavailable")
        }
        startStaleWatchdog()
    }

    public func disconnect() {
        link.onSnapshot = nil
        link.onReachabilityChange = nil
        staleWatchdog?.cancel()
        staleWatchdog = nil
        status = .offline
    }

    /// The phone can stay WatchConnectivity-reachable while its own upstream socket to the
    /// PC has silently died (e.g. the screen locked and iOS suspended it) — reachability
    /// alone can't see that. Poll for freshness so a stalled phone is reported as failed
    /// instead of parked at `.connected` forever, which is what lets `WatchTransport` fail
    /// over to a direct connection.
    private func startStaleWatchdog() {
        staleWatchdog?.cancel()
        let pollInterval = self.pollInterval
        let staleTimeout = self.staleTimeout
        staleWatchdog = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(pollInterval))
                guard !Task.isCancelled, let self else { return }
                guard case .connected = self.status else { continue }
                guard self.lastSnapshotAt != .distantPast else { continue }
                if Date.now.timeIntervalSince(self.lastSnapshotAt) > staleTimeout {
                    self.status = .failed("iPhone's connection is stale")
                }
            }
        }
    }

    public func send(_ command: ClientCommand) {
        link.send(command: command)
    }
}
