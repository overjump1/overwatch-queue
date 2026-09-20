import Foundation

/// What the update line at the bottom of the queue screen says. A check the user asked for says so
/// and says how it went; the six-hourly one in the background stays quiet unless it finds something.
enum UpdateNotice: Equatable {
    /// Nothing to say: no check has run, or a quiet background one found nothing.
    case quiet
    case checking
    case available(String)
    case upToDate
    case failed
}

/// Whether a newer build of the app is out, from the GitHub releases the Release workflow publishes.
///
/// iOS can't install an app on itself, so this only reports the version; getting the new build onto
/// the phone is still AltStore or Sideloadly with the IPA from the release page.
///
/// The version compared against comes from the IPA asset's filename, not the release tag:
/// release.yml only rebuilds the apps that changed and copies the rest of the assets forward, so
/// v2.0.42 can still hold OWQueue-2.0.40.ipa.
enum Updates {
    static let releaseAPI = URL(string: "https://api.github.com/repos/overjump1/overwatch-queue/releases/latest")!
    private static let assetPrefix = "OWQueue-"
    private static let assetSuffix = ".ipa"

    private struct Release: Decodable {
        let assets: [Asset]

        struct Asset: Decodable {
            let name: String
        }
    }

    static var current: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
    }

    /// `v2.0.42` becomes [2, 0, 42]. A trailing non-number ends it, so `0.0.0-dev` is [0, 0, 0].
    static func parse(_ version: String?) -> [Int]? {
        let trimmed = (version ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .drop { $0 == "v" || $0 == "V" }
        var parts: [Int] = []
        for piece in trimmed.split(whereSeparator: { $0 == "." || $0 == "-" || $0 == "+" }) {
            guard piece.allSatisfy(\.isNumber), let number = Int(piece) else { break }
            parts.append(number)
        }
        guard !parts.isEmpty else { return nil }
        return Array((parts + [0, 0, 0]).prefix(3))
    }

    static func isNewer(_ candidate: String?, than current: String?) -> Bool {
        guard let left = parse(candidate), let right = parse(current) else { return false }
        for (mine, theirs) in zip(left, right) where mine != theirs { return mine > theirs }
        return false
    }

    /// Off where a check would be wrong or pointless:
    /// - TestFlight and the App Store hand out their own updates, and their builds keep the
    ///   `MARKETING_VERSION` from project.yml, which is below every release and would claim an
    ///   update forever. A receipt on disk is what marks those builds.
    /// - Debug builds from Xcode, for the same reason.
    /// - Anything on a 0.x version, which is older than every release.
    /// Worked out once: the view body reads it, and it can't change while the app is running.
    static let checksForUpdates: Bool = {
        #if DEBUG
        return false
        #else
        if let receipt = Bundle.main.appStoreReceiptURL,
           FileManager.default.fileExists(atPath: receipt.path) {
            return false
        }
        return (parse(current)?.first ?? 0) > 0
        #endif
    }()

    /// Shaped like `Worker.Result`: "nothing newer" and "couldn't ask" are not the same answer.
    enum Outcome {
        case newer(String)
        case upToDate
        case failed
    }

    /// The version of the IPA in the latest release, if it's newer than this build.
    static func check() async -> Outcome {
        guard checksForUpdates else { return .upToDate }
        var request = URLRequest(url: releaseAPI, timeoutInterval: 20)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        // GitHub turns away requests without one.
        request.setValue("OWQueue/\(current)", forHTTPHeaderField: "User-Agent")
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200,
              let release = try? JSONDecoder().decode(Release.self, from: data)
        else { return .failed }
        guard let version = newerVersion(in: release.assets.map(\.name), than: current) else {
            return .upToDate
        }
        return .newer(version)
    }

    /// Split out from the request so the filename-over-tag rule stands on its own.
    static func newerVersion(in assetNames: [String], than current: String) -> String? {
        for name in assetNames where name.hasPrefix(assetPrefix) && name.hasSuffix(assetSuffix) {
            let version = String(name.dropFirst(assetPrefix.count).dropLast(assetSuffix.count))
            return isNewer(version, than: current) ? version : nil
        }
        return nil
    }
}
