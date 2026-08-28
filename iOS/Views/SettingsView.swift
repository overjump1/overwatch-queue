import SwiftUI

/// Which PC this phone listens to, and the two caches worth being able to see.
///
/// The controls that used to live here — start a queue, jump to a phase, play a
/// scenario — moved to the server's own window, where the real ones will be.
struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @Environment(QueueStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var confirmingUnpair = false
    @State private var showScanner = false

    var body: some View {
        NavigationStack {
            Form {
                connectionSection
                catalogSection
                cacheSection
            }
            .navigationTitle("Server")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .fullScreenCover(isPresented: $showScanner) {
                PairingView { showScanner = false }
                    .environment(model)
            }
            .alert("Unpair from this PC?", isPresented: $confirmingUnpair) {
                Button("Unpair", role: .destructive) {
                    model.unpair()
                    dismiss()
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("You'll need to scan the code again. The PC keeps the same code "
                     + "unless you generate a new one there.")
            }
        }
    }

    @ViewBuilder
    private var connectionSection: some View {
        Section {
            if let pairing = model.pairing {
                LabeledContent("PC", value: pairing.displayText)
                LabeledContent("Token", value: pairing.shortToken)
                    .font(.callout.monospaced())
                LabeledContent("Status") {
                    Label(store.status.displayText, systemImage: store.status.symbolName)
                        .foregroundStyle(store.status.isLive ? Palette.support : Palette.neutral)
                        .labelStyle(.titleAndIcon)
                }
                Button {
                    model.connect()
                } label: {
                    Label("Reconnect", systemImage: "arrow.clockwise")
                }
                Button {
                    showScanner = true
                } label: {
                    Label("Scan a code", systemImage: "qrcode.viewfinder")
                }
                Button(role: .destructive) {
                    confirmingUnpair = true
                } label: {
                    Label("Unpair", systemImage: "minus.circle")
                }
            } else {
                Text("Not paired with a PC.")
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text("Connection")
        } footer: {
            Text("Connects to ws://\(model.pairing?.displayText ?? "your-pc")/queue over "
                 + "your local network. See docs/PROTOCOL.md.")
        }
    }

    private var catalogSection: some View {
        Section("Catalog") {
            LabeledContent("Source", value: store.catalog.source.displayText)
            LabeledContent("Heroes", value: "\(store.catalog.heroes.count)")
            LabeledContent("Maps", value: "\(store.catalog.maps.count)")
            if let error = store.catalog.lastError {
                Text(error).font(.caption).foregroundStyle(Palette.damage)
            }
            Button {
                Task { await store.catalog.refresh() }
            } label: {
                Label("Refresh from OverFast", systemImage: "arrow.clockwise")
            }
            .disabled(store.catalog.isLoading)
        }
    }

    private var cacheSection: some View {
        Section {
            LabeledContent("On disk", value: byteText(ImageCache.shared.diskUsage))
            LabeledContent("In memory", value: byteText(ImageCache.shared.memoryUsage))
            Button(role: .destructive) {
                ImageCache.shared.clear()
            } label: {
                Label("Clear image cache", systemImage: "trash")
            }
        } header: {
            Text("Art cache")
        } footer: {
            Text("Hero portraits and map screenshots are cached on disk and kept decoded in "
                 + "memory, so they load once and then come back instantly — including "
                 + "after a relaunch.")
        }
    }

    private func byteText(_ bytes: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }
}
