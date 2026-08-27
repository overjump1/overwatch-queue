import Foundation

public enum TransportStatus: Hashable, Sendable {
    case offline
    case connecting
    case connected
    case failed(String)

    public var isLive: Bool { self == .connected }

    public var displayText: String {
        switch self {
        case .offline: return "Offline"
        case .connecting: return "Connecting…"
        case .connected: return "Connected"
        case .failed(let why): return why
        }
    }

    public var symbolName: String {
        switch self {
        case .offline: return "bolt.horizontal.circle"
        case .connecting: return "bolt.horizontal.circle.fill"
        case .connected: return "checkmark.circle.fill"
        case .failed: return "exclamationmark.triangle.fill"
        }
    }
}

/// Where queue state comes from. Everything above this line — the store, every view, the
/// Live Activity — is identical whether the state is coming from a real PC, from the
/// paired iPhone, or from the debug panel.
///
/// Main-actor bound on purpose: events land directly on an `@Observable` store, and the
/// concurrency this saves is worth more than the parallelism it gives up for a socket
/// that carries a handful of small messages per minute.
@MainActor
public protocol QueueTransport: AnyObject {
    var name: String { get }
    var status: TransportStatus { get }

    /// Called for every inbound event, on the main actor.
    var onEvent: ((QueueEvent) -> Void)? { get set }
    var onStatusChange: ((TransportStatus) -> Void)? { get set }

    func connect()
    func disconnect()
    /// Fire-and-forget. Transports queue or drop as appropriate for their medium and
    /// report failures through `onStatusChange` rather than throwing at the call site.
    func send(_ command: ClientCommand)
}
