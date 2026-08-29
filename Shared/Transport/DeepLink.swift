import Foundation

/// Every way a URL can arrive into the app, and what each one means.
///
/// There are two, and they need telling apart. `owq://pair?…` is the QR code — the way in
/// from the system camera. `owq://open` is the Live Activity and its Smart Stack mirror
/// asking to be brought forward, carrying nothing.
///
/// Before this existed, `onOpenURL` handed *every* URL to `Pairing(pairingCode:)`, so the
/// Live Activity's own tap target would have surfaced "That isn't a pairing code" on the
/// pairing screen. It never actually did, because the URL it carried used a scheme
/// (`owqueue`) the app doesn't register and so resolved to nothing at all — two bugs that
/// happened to cancel out. `openURL` below is built from `Pairing.scheme` rather than
/// spelled out a second time, which is what stops them drifting apart again.
public enum DeepLink: Hashable, Sendable {
    /// The QR code. Carries the raw string, because `Pairing`'s own parser is the one
    /// thing allowed to decide whether it's really a pairing code.
    case pair(String)
    /// Bring the app forward and show what's happening. No payload: the socket is the
    /// source of truth, and the snapshot it returns is more current than anything a URL
    /// could have carried.
    case open
    case unrecognised

    /// What the Live Activity's `widgetURL` points at, on every presentation.
    public static let openURL = URL(string: "\(Pairing.scheme)://\(Host.open)")!

    private enum Host {
        static let pair = "pair"
        static let open = "open"
    }

    public static func parse(_ url: URL) -> DeepLink {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme?.lowercased() == Pairing.scheme else { return .unrecognised }

        // `host` or bare `path`, matching `Pairing.init?(pairingCode:)` — a URL written
        // `owq:pair?…` rather than `owq://pair?…` parses with the word in `path`, and a
        // code that scans is a code that should work.
        switch components.host?.lowercased() ?? components.path.lowercased() {
        case Host.open: return .open
        case Host.pair: return .pair(url.absoluteString)
        default: return .unrecognised
        }
    }
}
