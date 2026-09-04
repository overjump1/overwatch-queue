import Foundation
import CocoaMQTT

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

    private var mqtt: CocoaMQTT?
    private var wantsConnection = false

    private static let phoneUsername = "owq"
    private static let keepAliveSeconds: UInt16 = 15

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
        if let mqtt, mqtt.connState == .connected {
            mqtt.publish(presenceTopic, withString: #"{"online":false}"#, qos: .qos1, retained: true)
            mqtt.disconnect()
        }
        mqtt = nil
        status = .offline
    }

    public func send(_ command: ClientCommand) {
        guard let mqtt, mqtt.connState == .connected else { return }
        do {
            let data = try Wire.encode(command)
            guard let text = String(data: data, encoding: .utf8) else { return }
            mqtt.publish(commandTopic, withString: text, qos: .qos1)
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
        let client = CocoaMQTT(clientID: clientID, host: pairing.host, port: UInt16(pairing.port))
        client.username = Self.phoneUsername
        client.password = pairing.token
        client.keepAlive = Self.keepAliveSeconds
        client.cleanSession = true
        client.autoReconnect = true
        client.autoReconnectTimeInterval = 5          // seconds between attempts
        client.willMessage = CocoaMQTTWill(topic: presenceTopic, message: #"{"online":false}"#)
        client.willMessage?.qos = .qos1
        client.willMessage?.retained = true
        client.delegate = self
        mqtt = client
        _ = client.connect()
    }

    private func subscribeAndAnnounce() {
        guard let mqtt else { return }
        mqtt.subscribe([
            (TOPIC_SNAPSHOT, CocoaMQTTQoS.qos1),
            (replyTopic, CocoaMQTTQoS.qos0),
            (TOPIC_HEARTBEAT, CocoaMQTTQoS.qos0),
        ])
        mqtt.publish(presenceTopic, withString: #"{"online":true}"#, qos: .qos1, retained: true)
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

extension MQTTTransport: CocoaMQTTDelegate {
    public func mqtt(_ mqtt: CocoaMQTT, didConnectAck ack: CocoaMQTTConnAck) {
        Task { @MainActor in
            guard ack == .accept else {
                // A rejected CONNACK (bad credentials, most likely a stale or revoked
                // pairing token) — `autoReconnect` would just retry the same wrong
                // password forever, so this is a terminal failure, not a transient one.
                self.status = .failed("Not paired with \(self.pairing.displayText) — re-scan the QR code.")
                self.wantsConnection = false
                mqtt.disconnect()
                // Fully torn down, not just told to disconnect: a later `disconnect()`
                // call (e.g. from unpairing right after a failed pairing attempt) would
                // otherwise reach into this same already-disconnected CocoaMQTT instance
                // again via `self.mqtt?...` and publish/disconnect on it a second time.
                self.mqtt = nil
                return
            }
            self.subscribeAndAnnounce()
            self.status = .connected
        }
    }

    public func mqtt(_ mqtt: CocoaMQTT, didReceiveMessage message: CocoaMQTTMessage, id: UInt16) {
        let topic = message.topic
        let payload = Data(message.payload)
        Task { @MainActor in self.handle(topic: topic, payload: payload) }
    }

    public func mqttDidDisconnect(_ mqtt: CocoaMQTT, withError err: Error?) {
        Task { @MainActor in
            guard self.wantsConnection else { return }
            self.status = .failed(err?.localizedDescription ?? "Disconnected from \(self.pairing.displayText)")
            // `autoReconnect` on the CocoaMQTT instance handles retrying the connection
            // itself; nothing to schedule here.
        }
    }

    public func mqtt(_ mqtt: CocoaMQTT, didPublishMessage message: CocoaMQTTMessage, id: UInt16) {}
    public func mqtt(_ mqtt: CocoaMQTT, didPublishAck id: UInt16) {}
    public func mqtt(_ mqtt: CocoaMQTT, didSubscribeTopics success: NSDictionary, failed: [String]) {}
    public func mqtt(_ mqtt: CocoaMQTT, didUnsubscribeTopics topics: [String]) {}
    public func mqttDidPing(_ mqtt: CocoaMQTT) {}
    public func mqttDidReceivePong(_ mqtt: CocoaMQTT) {}
}

private let TOPIC_SNAPSHOT = "owq/snapshot"
private let TOPIC_HEARTBEAT = "owq/heartbeat"
