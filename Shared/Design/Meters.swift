import SwiftUI

/// The estimated-wait bar. Once the wait runs past the estimate it stops pretending to
/// be a progress bar and becomes a shimmer — the honest signal that nobody knows how
/// much longer it'll be.
public struct WaitMeter: View {
    public var progress: Double?
    public var isOverdue: Bool
    public var tint: Color

    public init(progress: Double?, isOverdue: Bool, tint: Color) {
        self.progress = progress
        self.isOverdue = isOverdue
        self.tint = tint
    }

    public var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Palette.white.opacity(0.10))

                if isOverdue || progress == nil {
                    Capsule()
                        .fill(LinearGradient(colors: [tint.opacity(0.25), tint, tint.opacity(0.25)],
                                             startPoint: .leading, endPoint: .trailing))
                        .mask { ShimmerMask() }
                } else if let progress {
                    Capsule()
                        .fill(LinearGradient(colors: [tint.opacity(0.75), tint],
                                             startPoint: .leading, endPoint: .trailing))
                        .frame(width: max(6, geo.size.width * progress))
                        .shadow(color: tint.opacity(0.5), radius: 6, y: 1)
                        .animation(.smooth(duration: 0.8), value: progress)
                }
            }
        }
        .frame(height: 6)
    }
}

/// A highlight that sweeps left to right forever. Used to say "still working" where a
/// determinate bar would be a lie.
public struct ShimmerMask: View {
    public init() {}

    public var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30.0, paused: false)) { context in
            GeometryReader { geo in
                let period: Double = 1.8
                let t = context.date.timeIntervalSinceReferenceDate
                    .truncatingRemainder(dividingBy: period) / period
                let width = geo.size.width

                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0),
                        .init(color: .white, location: 0.5),
                        .init(color: .clear, location: 1),
                    ],
                    startPoint: .leading, endPoint: .trailing)
                .frame(width: width * 0.45)
                .offset(x: -width * 0.45 + t * (width * 1.45))
            }
        }
    }
}

/// A ring that empties as a deadline approaches, going red in the last quarter.
/// Self-ticking, so it costs nothing to keep on screen.
public struct CountdownRing: View {
    public var deadline: Date
    public var total: TimeInterval
    public var lineWidth: CGFloat
    public var tint: Color

    public init(deadline: Date, total: TimeInterval, lineWidth: CGFloat = 4, tint: Color = Palette.orange) {
        self.deadline = deadline
        self.total = max(1, total)
        self.lineWidth = lineWidth
        self.tint = tint
    }

    public var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 20.0, paused: false)) { context in
            let remaining = max(0, deadline.timeIntervalSince(context.date))
            let fraction = min(1, remaining / total)
            let urgent = fraction < 0.25

            ZStack {
                Circle().stroke(Palette.white.opacity(0.12), lineWidth: lineWidth)
                Circle()
                    .trim(from: 0, to: fraction)
                    .stroke(urgent ? Palette.damage : tint,
                            style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .shadow(color: (urgent ? Palette.damage : tint).opacity(0.7), radius: 6)
            }
        }
    }
}

/// Big self-ticking elapsed time. Uses `Text(timerInterval:)` so the system drives the
/// digits — no per-second view updates from our side.
public struct ElapsedTimer: View {
    public var since: Date
    public var size: CGFloat
    public var weight: Font.Weight

    public init(since: Date, size: CGFloat = 56, weight: Font.Weight = .bold) {
        self.since = since
        self.size = size
        self.weight = weight
    }

    public var body: some View {
        Text(timerInterval: since...Date.distantFuture, countsDown: false)
            .font(.system(size: size, weight: weight, design: .rounded))
            .monospacedDigit()
            .contentTransition(.numericText())
            .foregroundStyle(Palette.white)
    }
}

/// A countdown to `deadline` that the system ticks on its own.
///
/// `Text(timerInterval:)` takes a `ClosedRange`, and forming one whose upper bound has
/// already passed is a trap, not an empty range — so every countdown has to be guarded
/// against its own deadline expiring. That isn't an edge case here: hero select and the
/// map vote both outlive their deadline whenever the player uses the whole window, and a
/// Live Activity re-renders on its own schedule well after. Past the deadline this settles
/// on a static zero, which is what the running timer shows at the end anyway.
///
/// Returns `Text` rather than `some View` so callers keep the text-only modifiers —
/// `monospacedDigit()` above all — that the countdowns are styled with.
public extension Text {
    static func countdown(to deadline: Date) -> Text {
        // One `now` for both the comparison and the range: reading the clock twice lets a
        // deadline a hair in the future fall behind between them, which is the same trap
        // by a narrower door.
        let now = Date.now
        guard deadline > now else { return Text(verbatim: "0:00") }
        return Text(timerInterval: now...deadline, countsDown: true)
    }
}

/// `WaitMeter` for surfaces that cannot run a timeline.
///
/// A Live Activity's views are only re-rendered when the app pushes new content, so a
/// bar whose width is computed from `Date.now` freezes at whatever it was when the push
/// landed — during a search that means an empty bar for the whole queue while the phone
/// fills one every second. `ProgressView(timerInterval:)` is the one progress primitive
/// ActivityKit advances by itself, so this stays in step with the app for free.
///
/// The indeterminate case is a static gradient rather than `ShimmerMask`, because
/// `TimelineView` doesn't tick out here either and would just pin one frame of the sweep.
public struct TimedWaitMeter: View {
    public var start: Date
    /// When the estimate runs out. Nil when the server hasn't given one.
    public var estimatedEnd: Date?
    public var tint: Color

    public init(start: Date, estimatedEnd: Date?, tint: Color) {
        self.start = start
        self.estimatedEnd = estimatedEnd
        self.tint = tint
    }

    public var body: some View {
        Group {
            if let estimatedEnd, estimatedEnd > start, estimatedEnd > .now {
                ProgressView(timerInterval: start...estimatedEnd, countsDown: false) {
                    EmptyView()
                } currentValueLabel: {
                    EmptyView()
                }
                .progressViewStyle(.linear)
                .tint(tint)
            } else {
                // No estimate, or already past it — the same "nobody knows" treatment the
                // app shows, minus the animation this process can't drive.
                Capsule()
                    .fill(LinearGradient(colors: [tint.opacity(0.3), tint, tint.opacity(0.3)],
                                         startPoint: .leading, endPoint: .trailing))
            }
        }
        .frame(height: 6)
    }
}
