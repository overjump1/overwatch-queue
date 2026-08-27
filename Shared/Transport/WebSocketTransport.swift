import Foundation

/// Talks to the queue server on the PC over a WebSocket.
///
/// The server is expected at `ws://<host>:<port>/queue`, sending `WireEnvelope<QueueEvent>`
/// frames as text and accepting `WireEnvelope<ClientCommand>` frames back.
/// See `docs/PROTOCOL.md`.
@MainActor
public final class WebSocketTransport: NSObject, QueueTransport {
    public let name = "WebSocket"
    public private(set) var status: TransportStatus = .offline {
        didSet { if status != oldValue { onStatusChange?(status) } }
    }

    public var onEvent: ((QueueEvent) -> Void)?
    public var onStatusChange: ((TransportStatus) -> Void)?

    public var endpoint: Endpoint
    private let identity: ClientIdentity

    private var session: URLSession?
    private var task: URLSessionWebSocketTask?
    private var wantsConnection = false
    private var retryCount = 0
    private var reconnectTask: Task<Void, Never>?
    private var heartbeatTask: Task<Void, Never>?

    /// The server is considered gone if nothing arrives in this long. Set comfortably
    /// above the server's heartbeat interval so a slow frame doesn't cause a reconnect storm.
    private static let silenceTimeout: TimeInterval = 30
    private var lastInbound = Date.distantPast

    public struct Endpoint: Codable, Hashable, Sendable {
        public var host: String
        public var port: Int
        public var path: String

        public init(host: String = "192.168.1.10", port: Int = 8787, path: String = "/queue") {
            self.host = host
            self.port = port
            self.path = path
        }

        public var url: URL? {
            var c = URLComponents()
            c.scheme = "ws"
            c.host = host
            c.port = port
            c.path = path.hasPrefix("/") ? path : "/" + path
            return c.url
        }

        public var displayText: String { "\(host):\(port)" }
    }

    public init(endpoint: Endpoint, identity: ClientIdentity) {
        self.endpoint = endpoint
        self.identity = identity
        super.init()
    }

    public func connect() {
        wantsConnection = true
        openSocket()
    }

    public func disconnect() {
        wantsConnection = false
        reconnectTask?.cancel(); reconnectTask = nil
        heartbeatTask?.cancel(); heartbeatTask = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        status = .offline
    }

    public func send(_ command: ClientCommand) {
        guard let task else { return }
        do {
            let data = try Wire.encode(command)
            task.send(.data(data)) { [weak self] error in
                guard let error else { return }
                Task { @MainActor in self?.handleFailure(error) }
            }
        } catch {
            status = .failed("Couldn't encode command: \(error.localizedDescription)")
        }
    }

    // MARK: - Socket lifecycle

    private func openSocket() {
        guard let url = endpoint.url else {
            status = .failed("Bad address: \(endpoint.displayText)")
            return
        }
        status = .connecting
        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = false
        config.timeoutIntervalForRequest = 15
        let session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
        self.session = session

        let task = session.webSocketTask(with: url)
        self.task = task
        task.resume()
        lastInbound = .now
        receive()
        send(.hello(client: identity))
        startHeartbeatWatchdog()
    }

    private func receive() {
        task?.receive { [weak self] result in
            Task { @MainActor in
                guard let self else { return }
                switch result {
                case .success(let message):
                    self.lastInbound = .now
                    if self.status != .connected {
                        self.status = .connected
                        self.retryCount = 0
                    }
                    self.handle(message)
                    self.receive()          // re-arm; `receive` delivers exactly one message
                case .failure(let error):
                    self.handleFailure(error)
                }
            }
        }
    }

    private func handle(_ message: URLSessionWebSocketTask.Message) {
        let data: Data?
        switch message {
        case .data(let d): data = d
        case .string(let s): data = s.data(using: .utf8)
        @unknown default: data = nil
        }
        guard let data else { return }
        do {
            onEvent?(try Wire.decode(QueueEvent.self, from: data))
        } catch {
            // A single malformed frame shouldn't tear down a working connection —
            // surface it and keep listening.
            status = .failed(error.localizedDescription)
        }
    }

    private func handleFailure(_ error: Error) {
        guard wantsConnection else { return }
        status = .failed(error.localizedDescription)
        scheduleReconnect()
    }

    /// Exponential backoff capped at 30s, so a PC that's switched off doesn't get
    /// hammered and the phone's radio gets to sleep.
    private func scheduleReconnect() {
        guard wantsConnection, reconnectTask == nil else { return }
        let delay = min(30, pow(2, Double(min(retryCount, 5))))
        retryCount += 1
        task?.cancel(with: .abnormalClosure, reason: nil)
        task = nil
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard let self, !Task.isCancelled, self.wantsConnection else { return }
            self.reconnectTask = nil
            self.openSocket()
        }
    }

    /// A WebSocket can go quiet without erroring (sleeping laptop, dropped Wi-Fi). This
    /// notices the silence and forces a reconnect.
    private func startHeartbeatWatchdog() {
        heartbeatTask?.cancel()
        heartbeatTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                guard let self, self.wantsConnection else { return }
                if Date.now.timeIntervalSince(self.lastInbound) > Self.silenceTimeout {
                    self.status = .failed("No response from \(self.endpoint.displayText)")
                    self.scheduleReconnect()
                    return
                }
            }
        }
    }
}

extension WebSocketTransport: URLSessionWebSocketDelegate {
    nonisolated public func urlSession(_ session: URLSession,
                                       webSocketTask: URLSessionWebSocketTask,
                                       didOpenWithProtocol proto: String?) {
        Task { @MainActor in
            self.status = .connected
            self.retryCount = 0
        }
    }

    nonisolated public func urlSession(_ session: URLSession,
                                       webSocketTask: URLSessionWebSocketTask,
                                       didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
                                       reason: Data?) {
        Task { @MainActor in
            guard self.wantsConnection else { return }
            self.status = .failed("Server closed the connection")
            self.scheduleReconnect()
        }
    }
}
