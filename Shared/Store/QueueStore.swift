import Foundation
import Observation

/// The app's single source of truth. Owns the current snapshot, the transport, and the
/// clock correction; everything the UI renders comes from here.
///
/// Deliberately platform-neutral — the phone, the watch and the widgets all use this
/// same class. Platform-specific reactions (Live Activities, haptics, the watch relay)
/// hang off `onPhaseChange` instead of being baked in.
@MainActor
@Observable
public final class QueueStore {
    public private(set) var snapshot: QueueSnapshot = .idle
    public private(set) var status: TransportStatus = .offline
    public private(set) var clock = ClockSync()
    public private(set) var lastUpdate: Date = .distantPast

    /// Fires when the phase *kind* changes — not on every payload refresh, so a vote tally
    /// ticking up doesn't retrigger the match-found animation and haptic.
    public var onPhaseChange: ((QueuePhase, QueuePhase) -> Void)?
    /// Fires for every accepted snapshot, including payload-only refreshes. Used to keep
    /// the Live Activity and the watch in step.
    public var onSnapshot: ((QueueSnapshot) -> Void)?

    public let catalog: CatalogService
    public private(set) var transport: (any QueueTransport)?

    /// Set once this device has an APNs token to offer. Kept here, not in the transport,
    /// because it has to survive a transport being swapped out (relay → direct, or a
    /// fresh pairing) and resent to whichever one connects next.
    private var pendingPushToken: (token: String, environment: PushEnvironment)?
    /// The push token for whichever Live Activity is currently running, if any — one per
    /// session, replaced wholesale when a new activity starts.
    private var pendingActivityPushToken: (sessionID: UUID, token: String, environment: PushEnvironment)?
    /// The app-level push-to-start token — independent of any one session.
    private var pendingActivityStartToken: (token: String, environment: PushEnvironment)?

    public var phase: QueuePhase { snapshot.phase }

    public init(catalog: CatalogService = .shared) {
        self.catalog = catalog
    }

    // MARK: - Transport

    /// Swaps the state source — used when a new PC is paired, without restarting the app.
    public func use(_ transport: any QueueTransport) {
        release()

        self.transport = transport
        transport.onEvent = { [weak self] event in self?.handle(event) }
        transport.onStatusChange = { [weak self] status in
            self?.status = status
            if status.isLive {
                self?.sendPendingRegistrations()
                self?.syncClock()
            }
        }
        status = transport.status
        transport.connect()
    }

    /// Registers this device's APNs token with whichever transport is live — resent
    /// automatically on every future (re)connect, since a relay swap or a fresh pairing
    /// means a new socket that has never heard about it.
    public func registerPushToken(_ token: String, environment: PushEnvironment) {
        pendingPushToken = (token, environment)
        sendPendingRegistrations()
    }

    /// Registers the push token for one running Live Activity — see
    /// `ClientCommand.registerActivityPushToken`.
    public func registerActivityPushToken(sessionID: UUID, token: String, environment: PushEnvironment) {
        pendingActivityPushToken = (sessionID, token, environment)
        sendPendingRegistrations()
    }

    /// Registers the app-level push-to-start token — see
    /// `ClientCommand.registerActivityStartToken`.
    public func registerActivityStartToken(_ token: String, environment: PushEnvironment) {
        pendingActivityStartToken = (token, environment)
        sendPendingRegistrations()
    }

    /// Asks the server what time it is, and times the answer.
    ///
    /// A burst rather than one, because the useful thing about a round trip is that a
    /// short one bounds the error — and on a shared Wi-Fi link the shortest of a few
    /// attempts is a much better bound than whichever one happened to go first.
    /// `ClockSync.verify` keeps the sharpest and discards the rest.
    ///
    /// Sent on connect, and again whenever the app comes back to the foreground: a device
    /// that has been asleep is exactly where a stale reading used to take hold.
    public func syncClock(samples: Int = 3, spacing: Duration = .milliseconds(250)) {
        guard status.isLive else { return }
        Task { [weak self] in
            for attempt in 0..<samples {
                guard let self, self.status.isLive else { return }
                self.transport?.send(.ping(clientTime: Date().timeIntervalSince1970))
                if attempt < samples - 1 { try? await Task.sleep(for: spacing) }
            }
        }
    }

    /// Re-anchors the clock, and says so when the anchor actually moves.
    ///
    /// Only the heartbeat feeds this. The offset is measured as `serverTime - now`, so it
    /// absorbs however long the message took to arrive — which makes freshness, not
    /// content, the thing that qualifies a message as a clock sample. A heartbeat is
    /// fresh by construction: it is generated and sent in the same breath, ten seconds
    /// apart, and only over a socket that is currently up. A snapshot is not, and is
    /// therefore not used for this.
    ///
    /// A watch on the phone relay consequently gets no correction at all, and shouldn't:
    /// with no live socket of its own there is nothing fresh to measure against, and a
    /// zero offset — trusting that a watch and a PC are both keeping ordinary network
    /// time — is far closer to right than a reading taken off a coalesced snapshot of
    /// unknown age. When the watch does open its own socket, heartbeats correct it
    /// properly.
    private func observeClock(serverTime: Date, from source: String) {
        let before = clock.offset
        let candidate = serverTime.timeIntervalSince(.now)
        clock.observe(serverTime: serverTime)
        if clock.offset != before {
            report(String(format: "clock re-anchored from %@: %+.1fs (was %+.1fs, sample %+.1fs)",
                          source, clock.offset, before, candidate))
        } else if abs(candidate - clock.offset) > ClockSync.deadband {
            report(String(format: "clock held at %+.1fs, ignoring %@ sample %+.1fs",
                          clock.offset, source, candidate))
        }
    }

    /// Writes a line into the server's log — see `ClientCommand.diagnostic`.
    ///
    /// Dropped rather than queued when nothing is connected: a diagnostic is only worth
    /// anything next to the moment it describes, and a backlog delivered minutes later
    /// out of order would be worse than the silence it replaced.
    public func report(_ message: @autoclosure () -> String) {
        #if DEBUG
        guard status.isLive else { return }
        transport?.send(.diagnostic(message()))
        #endif
    }

    private func sendPendingRegistrations() {
        guard status.isLive else { return }
        if let pending = pendingPushToken {
            transport?.send(.registerPushToken(token: pending.token, environment: pending.environment))
        }
        if let pending = pendingActivityPushToken {
            transport?.send(.registerActivityPushToken(sessionID: pending.sessionID, token: pending.token,
                                                        environment: pending.environment))
        }
        if let pending = pendingActivityStartToken {
            transport?.send(.registerActivityStartToken(token: pending.token, environment: pending.environment))
        }
    }

    /// Drops the transport and goes quiet. The last snapshot stays put; unpairing is
    /// what clears the screen, and that resets the store separately.
    public func disconnect() {
        release()
        transport = nil
        status = .offline
    }

    /// Back to idle. Unpairing calls this — a screen we no longer trust the source of
    /// shouldn't keep showing the last thing it said.
    public func reset() {
        snapshot = .idle
        clock = ClockSync()
        lastUpdate = .distantPast
    }

    private func release() {
        transport?.disconnect()
        transport?.onEvent = nil
        transport?.onStatusChange = nil
    }

    public func handle(_ event: QueueEvent) {
        switch event {
        case .snapshot(let incoming):
            ingest(incoming)
        case .heartbeat(let serverTime):
            observeClock(serverTime: serverTime, from: "heartbeat")
            lastUpdate = .now
        case .pong(let clientTime, let serverTime):
            let before = clock.offset
            clock.verify(clientTime: clientTime, serverTime: serverTime)
            if clock.offset != before || !clock.isVerified {
                report(String(format: "clock verified: offset %+.2fs ±%.2fs (was %+.2fs)",
                              clock.offset, clock.uncertainty / 2, before))
            }
            lastUpdate = .now
        case .error(_, let message):
            status = .failed(message)
        }
    }

    /// Applies a snapshot if it's actually newer. WCSession gives no ordering guarantee
    /// and a reconnecting socket can replay, so this check is what keeps the UI from
    /// jumping backwards.
    public func ingest(_ incoming: QueueSnapshot) {
        guard snapshot.supersededBy(incoming) || snapshot.sequence == 0 else { return }
        let previous = snapshot.phase
        // Deliberately *not* a clock sample. A snapshot is kept and re-delivered: iOS
        // hands a resumed app its socket backlog, and WatchConnectivity coalesces and
        // delivers whenever it likes — so `serverTime` here can be many seconds old with
        // nothing to say so, and believing it sets every timer on this device that far
        // out. The watch felt this worst, because the very first thing it ever sees is a
        // relayed snapshot: it would open a thirteen-second-old queue reading one second.
        // `heartbeat` is the sample to trust — see `observeClock`.
        snapshot = incoming
        lastUpdate = .now

        if previous.kind != incoming.phase.kind {
            onPhaseChange?(previous, incoming.phase)
        }
        onSnapshot?(incoming)
    }

    // MARK: - Player actions

    /// Records the vote locally for instant feedback and sends it upstream. The optimistic
    /// local update is overwritten by the server's next snapshot if it disagrees.
    public func vote(map key: String) {
        if case .mapVote(var info) = snapshot.phase, info.myVote != key {
            info.myVote = key
            snapshot = snapshot.advanced(to: .mapVote(info), now: clock.remoteNow())
        }
        transport?.send(.voteMap(mapKey: key))
    }

    public func select(hero key: String) {
        if case .heroSelect(var info) = snapshot.phase, info.myHeroKey != key {
            info.myHeroKey = key
            snapshot = snapshot.advanced(to: .heroSelect(info), now: clock.remoteNow())
        }
        transport?.send(.selectHero(heroKey: key))
    }

    /// Leaves the queue, or tries to bail out of a match that's already been found.
    /// The latter is not guaranteed to work — see `ClientCommand.cancelQueue`.
    public func cancelQueue() { transport?.send(.cancelQueue) }
    public func requestRefresh() { transport?.send(.requestSnapshot) }

    // MARK: - Derived values

    /// Corrected for clock drift, so a PC running a few seconds fast doesn't inflate the
    /// wait time shown on the phone.
    public func localDeadline() -> Date? {
        phase.deadline.map { clock.toLocal($0) }
    }

    public func localQueueStart() -> Date? {
        guard case .searching(let info) = phase else { return nil }
        return clock.toLocal(info.startedAt)
    }

    /// The map the player is heading to, once one is known.
    public var currentMap: OverwatchMap? {
        switch phase {
        case .heroSelect(let i): return catalog.map(i.mapKey)
        case .inGame(let i): return catalog.map(i.mapKey)
        case .mapVote(let i): return catalog.map(i.myVote ?? i.leader?.mapKey)
        default: return nil
        }
    }

    public var currentHero: Hero? {
        switch phase {
        case .heroSelect(let i): return catalog.hero(i.myHeroKey)
        case .inGame(let i): return catalog.hero(i.heroKey)
        default: return nil
        }
    }

    /// Heroes offered in the current hero-select, minus ones teammates already locked.
    ///
    /// When the server has actually read the roster off the screen, that reading wins over
    /// the catalog: it is the only source that knows what this particular queue is
    /// offering. `availableHeroKeys` being nil means nobody looked — not that the roster
    /// is empty — so the catalog is used unfiltered in that case.
    public func selectableHeroes() -> [Hero] {
        guard case .heroSelect(let info) = phase else { return [] }
        let taken = Set(info.takenHeroKeys)
        let heroes = catalog.heroes(role: info.role, mode: info.mode)
            .filter { !taken.contains($0.key) }
        guard let available = info.availableHeroKeys else { return heroes }
        let onScreen = Set(available)
        let seen = heroes.filter { onScreen.contains($0.key) }
        // A scan that recognised nothing shouldn't blank the screen — better the full
        // catalog than an empty grid the player can't pick from.
        return seen.isEmpty ? heroes : seen
    }

    /// Everyone's pick, as the server last read it off the screen, in slot order.
    ///
    /// Empty when the server hasn't looked. Slot numbers are screen positions, not roles:
    /// role queue orders them by role, so the player isn't reliably first — `isSelf` is
    /// what marks our own pick, and nothing marks it when the scan couldn't tell.
    public func teamPicks() -> [(pick: TeamPick, hero: Hero)] {
        guard case .heroSelect(let info) = phase, let picks = info.teamPicks else { return [] }
        return picks.sorted { $0.slot < $1.slot }.compactMap { pick in
            guard let hero = catalog.hero(pick.heroKey) else { return nil }
            return (pick, hero)
        }
    }
}
