import SwiftUI
import WidgetKit

/// Timeline provider and view shared by the iOS Home Screen widget and the watch Smart
/// Stack widget. Only the widget *declarations* differ between platforms (families
/// available on one don't exist on the other), so those stay in their own targets.
public enum QueueStatus {
    struct Entry: TimelineEntry {
        let date: Date
        let snapshot: QueueSnapshot
    }

    struct Provider: TimelineProvider {
        func placeholder(in context: Context) -> Entry {
            Entry(date: .now, snapshot: QueueSnapshot(
                phase: .searching(SearchInfo(mode: .competitive, role: .tank,
                                             startedAt: .now.addingTimeInterval(-125),
                                             estimatedWait: 240))))
        }

        func getSnapshot(in context: Context, completion: @escaping (Entry) -> Void) {
            completion(Entry(date: .now, snapshot: SharedState.read() ?? .idle))
        }

        func getTimeline(in context: Context, completion: @escaping (Timeline<Entry>) -> Void) {
            let snapshot = SharedState.read() ?? .idle
            // The app reloads timelines on every state change, so this only needs a slow
            // safety-net refresh rather than a dense schedule that would burn budget.
            let next = Date.now.addingTimeInterval(snapshot.phase.kind == .idle ? 30 * 60 : 5 * 60)
            completion(Timeline(entries: [Entry(date: .now, snapshot: snapshot)], policy: .after(next)))
        }
    }
}

struct QueueStatusWidgetView: View {
    var snapshot: QueueSnapshot
    @Environment(\.widgetFamily) private var family

    private var phase: QueuePhase { snapshot.phase }
    private var accent: Color { Palette.accent(for: phase) }

    var body: some View {
        switch family {
        case .accessoryCircular:
            ZStack {
                AccessoryWidgetBackground()
                VStack(spacing: 0) {
                    Image(systemName: symbolName).font(.caption)
                    timer.font(.system(size: 12, weight: .bold, design: .rounded))
                }
            }
        case .accessoryRectangular:
            VStack(alignment: .leading, spacing: 1) {
                Text(headline).font(.caption.weight(.semibold)).lineLimit(1)
                timer.font(.system(.title3, design: .rounded, weight: .bold)).monospacedDigit()
            }
        default:
            VStack(alignment: .leading, spacing: 8) {
                Image(systemName: symbolName)
                    .font(.title2)
                    .foregroundStyle(accent)
                Spacer()
                timer
                    .font(.system(size: 30, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(Palette.white)
                Text(headline)
                    .font(.caption2)
                    .foregroundStyle(Palette.white.opacity(0.6))
                    .lineLimit(2)
            }
        }
    }

    @ViewBuilder
    private var timer: some View {
        if let deadline = phase.deadline {
            Text(timerInterval: Date.now...deadline, countsDown: true)
        } else if case .searching(let info) = phase {
            Text(timerInterval: info.startedAt...Date.distantFuture, countsDown: false)
        } else if case .inGame(let info) = phase {
            Text(timerInterval: info.startedAt...Date.distantFuture, countsDown: false)
        } else {
            Text("—")
        }
    }

    private var symbolName: String {
        switch phase {
        case .idle: return "moon.zzz.fill"
        case .searching(let i): return i.role.symbolName
        case .matchFound: return "bolt.fill"
        case .mapVote: return "map.fill"
        case .heroSelect: return "person.crop.square.fill"
        case .inGame: return "gamecontroller.fill"
        case .cancelled: return "xmark.circle.fill"
        }
    }

    private var headline: String {
        switch phase {
        case .idle: return "Not queued"
        case .searching(let i): return "\(i.mode.displayName) · \(i.role.displayName)"
        case .matchFound: return "Match found"
        case .mapVote: return "Map vote open"
        case .heroSelect: return "Hero select"
        case .inGame: return "In game"
        case .cancelled(let i): return i.displayText
        }
    }
}
