import Foundation

/// The complete state of the queue at one instant. This is what crosses every boundary:
/// server → phone, phone → watch, app → Live Activity. Anything not in here doesn't exist
/// as far as the UI is concerned.
public struct QueueSnapshot: Codable, Hashable, Sendable {
    /// Identifies one continuous queue session, so a stale snapshot from a previous
    /// session can be recognised and dropped.
    public var sessionID: UUID
    /// Monotonically increasing per session. Out-of-order deliveries (WCSession makes no
    /// ordering promise) are discarded by comparing this.
    public var sequence: Int
    public var phase: QueuePhase
    /// The sending clock's idea of "now", used to correct for drift between the PC and
    /// the phone. See `ClockSync`.
    public var serverTime: Date

    public init(sessionID: UUID = UUID(), sequence: Int = 0,
                phase: QueuePhase = .idle, serverTime: Date = .now) {
        self.sessionID = sessionID
        self.sequence = sequence
        self.phase = phase
        self.serverTime = serverTime
    }

    public static let idle = QueueSnapshot()

    /// Whether `other` should replace this snapshot. A new session always wins; within a
    /// session, only a newer sequence does.
    public func supersededBy(_ other: QueueSnapshot) -> Bool {
        other.sessionID != sessionID || other.sequence > sequence
    }

    public func advanced(to phase: QueuePhase, now: Date = .now) -> QueueSnapshot {
        QueueSnapshot(sessionID: sessionID, sequence: sequence + 1,
                      phase: phase, serverTime: now)
    }
}

/// Corrects timers for drift between the reporting clock and this device.
///
/// Without this, a Windows box a few seconds off would make "waited 3:02" read wrong on
/// the phone, and a Live Activity would tick from the wrong origin for the whole queue.
public struct ClockSync: Sendable {
    /// remoteTime - localTime, in seconds.
    public private(set) var offset: TimeInterval = 0

    /// Ignore corrections smaller than this; they're noise from network latency and
    /// re-anchoring on them makes timers visibly stutter.
    public static let deadband: TimeInterval = 1.5

    /// How long a good sample is trusted before a worse one may replace it. Long enough
    /// that no plausible delivery delay outlasts it, short enough that a server whose
    /// clock genuinely moved is believed before anyone notices.
    public static let anchorLifetime: TimeInterval = 120

    /// When the sample currently anchoring `offset` arrived. `nil` until the first one.
    private var anchoredAt: Date?

    public init(offset: TimeInterval = 0) {
        self.offset = offset
    }

    /// Folds in one observation of the server's clock.
    ///
    /// Not simply "believe the newest", which is what this used to do and what made a
    /// queue read differently on each device. `serverTime` is stamped before the message
    /// travels and `receivedAt` is taken after, so every sample is understated by exactly
    /// however long delivery took: `measured = true − latency`. Latency is never negative,
    /// so **the largest sample seen is the truest one**, and the newest is merely the
    /// most recent — a distinction that costs nothing on a socket answering in
    /// milliseconds and everything the moment something buffers.
    ///
    /// Both things that surface a queue do buffer. iOS suspends a backgrounded app and
    /// hands it the socket's backlog on resume, and WatchConnectivity coalesces and
    /// delivers on its own schedule; measured here, a snapshot thirteen seconds stale is
    /// indistinguishable from a PC thirteen seconds slow, and the old code took it at
    /// face value and re-anchored every timer onto it.
    ///
    /// So a sample is adopted when it's *better* — less latency, a larger offset — and
    /// otherwise only once the current anchor has aged out, which is what still lets a
    /// server whose clock really did change be believed eventually.
    public mutating func observe(serverTime: Date, receivedAt: Date = .now) {
        let candidate = serverTime.timeIntervalSince(receivedAt)

        guard let anchoredAt else {
            // Nothing has anchored yet, so there's no "better" to measure against —
            // take this one, unless it agrees with where we already are.
            if abs(candidate - offset) > Self.deadband { offset = candidate }
            self.anchoredAt = receivedAt
            return
        }

        let isBetter = candidate > offset + Self.deadband
        let isWorse = candidate < offset - Self.deadband
        let anchorHasAgedOut = receivedAt.timeIntervalSince(anchoredAt) > Self.anchorLifetime

        if isBetter || (isWorse && anchorHasAgedOut) {
            offset = candidate
            self.anchoredAt = receivedAt
        }
    }

    /// Converts a timestamp from the server's clock into this device's clock.
    public func toLocal(_ remote: Date) -> Date {
        remote.addingTimeInterval(-offset)
    }

    /// This device's "now" expressed on the server's clock.
    public func remoteNow(_ local: Date = .now) -> Date {
        local.addingTimeInterval(offset)
    }
}
