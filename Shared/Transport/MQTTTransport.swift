import Foundation

/// Talks to the queue server on the PC over MQTT, via the local Mosquitto broker the
/// server itself manages (see `server/owqserver/mqttbroker.py`). Replaces
/// `WebSocketTransport` — see `docs/PROTOCOL.md` for the topic layout.
///
/// The pairing token is this connection's MQTT password, not a payload inside `hello`
/// any more: a bad token is refused by the broker at CONNECT time, before a single byte
/// of state reaches this device or leaves it. `owq/snapshot` is retained, so subscribing
/// with the right credentials is what hands back the current state instantly — including
/// after a reconnect following a dead Wi-Fi, which is the whole reason this replaced the
/// old hand-rolled WebSocket server: a write failure there silently dropped a client's
/// next update with nothing to redeliver it.
///
/// Built on the vendored `NextMQTT` client (see `Shared/Transport/NextMQTT/`), not
/// CocoaMQTT: this transport also runs on watchOS (the watch falls back to it directly
/// when the phone is unreachable), and CocoaMQTT's socket layer doesn't build there.
@MainActor
public final class MQTTTransport: NSObject, QueueTransport {
    public let name = "MQTT"
    public private(set) var status: TransportStatus = .offline {
        didSet { if status != oldValue { onStatusChange?(status) } }
    }

    public var onEvent: ((QueueEvent) -> Void)?
    public var onStatusChange: ((TransportStatus) -> Void)?

    public var pairing: Pairing
    private let identity: ClientIdentity
    private let clientID: String

    private var mqtt: MQTT?
    private var wantsConnection = false

    private static let phoneUsername = "owq"
    private static let keepAliveSeconds = 15

    public init(pairing: Pairing, identity: ClientIdentity) {
        self.pairing = pairing
        self.identity = identity
        // Stable per install, not per launch: a fresh clientID every launch would leave
        // the broker's retained `owq/presence/<old-id>` topic permanently claiming a
        // device is still online that in fact just restarted under a new identity.
        self.clientID = MQTTTransport.persistentClientID()
        super.init()
    }

    public func connect() {
        wantsConnection = true
        openConnection()
    }

    public func disconnect() {
        wantsConnection = false
        // A clean disconnect, not the Last Will path: this device really is going
        // offline on purpose, so publish that now rather than waiting on the broker to
        // notice the socket died. Only meaningful — and only safe to attempt — while
        // actually connected; a transport that never got past a rejected CONNACK (or is
        // already mid-teardown) has nothing to publish through.
        if let mqtt, mqtt.connectionState == .connected {
            mqtt.publish(to: presenceTopic, qos: .leastOnce, retain: true, message: Self.presenceMessage(online: false))
            mqtt.disconnect()
        }
        mqtt = nil
        status = .offline
    }

    public func send(_ command: ClientCommand) {
        guard let mqtt, mqtt.connectionState == .connected else { return }
        do {
            let data = try Wire.encode(command)
            mqtt.publish(to: commandTopic, qos: .leastOnce, message: data)
        } catch {
            status = .failed("Couldn't encode command: \(error.localizedDescription)")
        }
    }

    // MARK: - Connection lifecycle

    private var commandTopic: String { "owq/command/\(clientID)" }
    private var replyTopic: String { "owq/reply/\(clientID)" }
    private var presenceTopic: String { "owq/presence/\(clientID)" }

    private func openConnection() {
        status = .connecting
        let will = MQTT.Will(topic: presenceTopic, message: #"{"online":false}"#, qos: .leastOnce, retain: true)
        let client = MQTT(
            host: pairing.host,
            port: pairing.port,
            username: Self.phoneUsername,
            password: pairing.token,
            will: will,
            options: [.clientId: clientID, .cleanStart: true, .pingInterval: Self.keepAliveSeconds]
        )
        client.onMessage = { [weak self] topic, payload in
            guard let self, let payload else { return }
            Task { @MainActor in self.handle(topic: topic, payload: payload) }
        }
        client.onConnectionState = { [weak self] state in
            Task { @MainActor in self?.handleConnectionState(state) }
        }
        mqtt = client
        client.connect { [weak self] result in
            guard let self else { return }
            Task { @MainActor in
                guard case .failure = result else { return }
                // A rejected CONNACK (bad credentials, most likely a stale or revoked
                // pairing token) on the *initial* connect attempt — retrying with the
                // same wrong password would never succeed, so this is a terminal
                // failure, not a transient one.
                self.status = .failed("Not paired with \(self.pairing.displayText) — re-scan the QR code.")
                self.wantsConnection = false
                self.mqtt?.disconnect()
            }
        }
    }

    private func handleConnectionState(_ state: MQTT.ConnectionState) {
        switch state {
        case .connecting, .reconnecting:
            status = .connecting
        case .connected:
            subscribeAndAnnounce()
            status = .connected
        case .dropped:
            guard wantsConnection else { return }
            // The client's own auto-reconnect loop handles retrying; nothing to
            // schedule here.
            status = .failed("Disconnected from \(pairing.displayText)")
        case .notConnected, .disconnecting, .disconnected:
            break
        }
    }

    private func subscribeAndAnnounce() {
        guard let mqtt else { return }
        mqtt.subscribe(to: TOPIC_SNAPSHOT, options: [.qos(.leastOnce), .retainSendOnSubscribe])
        mqtt.subscribe(to: replyTopic, options: [.qos(.mostOnce), .retainSendOnSubscribe])
        mqtt.subscribe(to: TOPIC_HEARTBEAT, options: [.qos(.mostOnce), .retainSendOnSubscribe])
        mqtt.publish(to: presenceTopic, qos: .leastOnce, retain: true, message: Self.presenceMessage(online: true))
        send(.hello(client: identity, token: nil))
    }

    private func handle(topic: String, payload: Data) {
        do {
            onEvent?(try Wire.decode(QueueEvent.self, from: payload))
        } catch {
            // A single malformed message shouldn't tear down a working connection —
            // surface it and keep listening.
            status = .failed(error.localizedDescription)
        }
    }

    private static func presenceMessage(online: Bool) -> Data {
        Data(#"{"online":\#(online)}"#.utf8)
    }

    /// A clientID that survives relaunches (so the broker's retained presence topic
    /// doesn't keep pointing at a stale "online" for a device that quit) but is unique
    /// per install (so two phones paired to the same PC don't collide on one topic set).
    private static func persistentClientID() -> String {
        let key = "mqttClientID"
        let defaults = UserDefaults(suiteName: SharedState.appGroup) ?? .standard
        if let existing = defaults.string(forKey: key) { return existing }
        let generated = "owq-\(UUID().uuidString.prefix(12))"
        defaults.set(generated, forKey: key)
        return generated
    }
}

private let TOPIC_SNAPSHOT = "owq/snapshot"
private let TOPIC_HEARTBEAT = "owq/heartbeat"
