import AVFoundation
import SwiftUI

/// The whole of setup: point the camera at the code in the server window.
///
/// This is what an unpaired app shows instead of a queue. There is no manual "type the
/// IP" as the primary path — the token can't be typed from memory anyway — but the code
/// can be pasted, which is how you pair a Simulator that has no camera to point.
struct PairingView: View {
    @Environment(AppModel.self) private var model

    @State private var scanner = ScannerModel()
    @State private var problem: String?
    @State private var showManualEntry = false

    var body: some View {
        ZStack {
            MeshBackground(phase: .idle, intensity: 0.7)

            VStack(spacing: 0) {
                header
                viewfinder
                    .frame(maxWidth: .infinity)
                    .aspectRatio(1, contentMode: .fit)
                    .padding(.horizontal, 36)
                    .padding(.top, 8)

                message
                    .frame(height: 52)
                    .padding(.horizontal, 32)

                Spacer(minLength: 0)
                actions
                    .padding(.horizontal, 32)
                    .padding(.bottom, 40)
            }
        }
        .sheet(isPresented: $showManualEntry) {
            ManualPairingView { accept($0) }
        }
        .task { await scanner.start() }
        .onDisappear { scanner.stop() }
        .onChange(of: scanner.scannedCode) { _, code in
            guard let code else { return }
            accept(code)
        }
    }

    // MARK: - Pieces

    private var header: some View {
        VStack(spacing: 10) {
            Image(systemName: "qrcode.viewfinder")
                .font(.system(size: 40, weight: .light))
                .foregroundStyle(Palette.orange)

            Text("Pair with your PC")
                .font(.title2.weight(.semibold))
                .foregroundStyle(Palette.white)

            Text("Run the queue server on your PC and point this at the code it shows.")
                .font(.subheadline)
                .foregroundStyle(Palette.white.opacity(0.55))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 44)
        }
        .padding(.top, 44)
    }

    @ViewBuilder
    private var viewfinder: some View {
        ZStack {
            switch scanner.state {
            case .running:
                CameraPreview(session: scanner.session)
            case .denied:
                unavailable("Camera access is off",
                            "Turn it on in Settings, or paste the pairing link instead.",
                            "camera.badge.ellipsis")
            case .unavailable:
                unavailable("No camera here",
                            "Copy the pairing link from the server window and paste it below.",
                            "desktopcomputer")
            case .starting:
                ProgressView().tint(Palette.white)
            }
        }
        .background(Palette.night.opacity(0.7))
        .clipShape(RoundedRectangle(cornerRadius: 28, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .strokeBorder(Palette.white.opacity(0.14), lineWidth: 1)
        }
    }

    private func unavailable(_ title: String, _ detail: String, _ symbol: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 34, weight: .light))
                .foregroundStyle(Palette.neutral)
            Text(title)
                .font(.headline)
                .foregroundStyle(Palette.white.opacity(0.9))
            Text(detail)
                .font(.footnote)
                .foregroundStyle(Palette.white.opacity(0.5))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 28)
        }
    }

    @ViewBuilder
    private var message: some View {
        if let problem {
            Label(problem, systemImage: "exclamationmark.triangle.fill")
                .font(.footnote.weight(.medium))
                .foregroundStyle(Palette.damage)
                .multilineTextAlignment(.center)
                .transition(.opacity)
        }
    }

    private var actions: some View {
        VStack(spacing: 12) {
            Button {
                paste()
            } label: {
                Label("Paste pairing link", systemImage: "doc.on.clipboard")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(Palette.orange)

            Button("Enter it by hand") { showManualEntry = true }
                .font(.footnote)
                .foregroundStyle(Palette.white.opacity(0.55))
        }
    }

    // MARK: - Pairing

    private func paste() {
        guard let text = UIPasteboard.general.string else {
            report("Nothing on the clipboard yet — press Copy pairing link on the PC.")
            return
        }
        accept(text)
    }

    private func accept(_ code: String) {
        guard model.pair(with: code) else {
            scanner.rearm()
            report("That isn't a pairing code from the queue server.")
            return
        }
        Haptics.attention()
        scanner.stop()
    }

    private func report(_ text: String) {
        withAnimation { problem = text }
        Haptics.warning()
    }
}

// MARK: - Manual entry

/// The fallback when there's no camera and no clipboard: the three fields the code
/// carries, typed out. The token is long on purpose, so this is the last resort rather
/// than the front door.
private struct ManualPairingView: View {
    var onPair: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var host = ""
    @State private var port = String(Pairing.defaultPort)
    @State private var token = ""

    private var isComplete: Bool {
        !host.trimmed.isEmpty && !token.trimmed.isEmpty && Int(port) != nil
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    LabeledContent("PC address") {
                        TextField("192.168.1.14", text: $host)
                            .multilineTextAlignment(.trailing)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.numbersAndPunctuation)
                    }
                    LabeledContent("Port") {
                        TextField("8787", text: $port)
                            .multilineTextAlignment(.trailing)
                            .keyboardType(.numberPad)
                    }
                } footer: {
                    Text("Both are printed under the QR code in the server window.")
                }

                Section {
                    TextField("Pairing token", text: $token, axis: .vertical)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.footnote.monospaced())
                } footer: {
                    Text("The token is the long value in the server window's pairing link. "
                         + "Scanning the code is far less work.")
                }
            }
            .navigationTitle("Enter by hand")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Pair") {
                        onPair("\(Pairing.scheme)://pair?host=\(host.trimmed)"
                               + "&port=\(port)&token=\(token.trimmed)")
                        dismiss()
                    }
                    .disabled(!isComplete)
                }
            }
        }
    }
}

private extension String {
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}

// MARK: - Camera

/// Owns the capture session and hands back the first QR payload it sees.
@MainActor
@Observable
final class ScannerModel: NSObject, AVCaptureMetadataOutputObjectsDelegate {
    enum State { case starting, running, denied, unavailable }

    private(set) var state: State = .starting
    private(set) var scannedCode: String?

    let session = AVCaptureSession()
    private var isConfigured = false

    func start() async {
        guard await hasPermission() else {
            state = .denied
            return
        }
        guard configure() else {
            state = .unavailable
            return
        }
        state = .running
        // Starting the session blocks for a moment, which would stutter the transition in.
        let session = session
        await Task.detached(priority: .userInitiated) {
            if !session.isRunning { session.startRunning() }
        }.value
    }

    func stop() {
        let session = session
        Task.detached(priority: .utility) {
            if session.isRunning { session.stopRunning() }
        }
    }

    /// After a code that turned out not to be ours, so the next one is still read.
    func rearm() {
        scannedCode = nil
    }

    private func hasPermission() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return true
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .video)
        default: return false
        }
    }

    private func configure() -> Bool {
        if isConfigured { return true }
        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera,
                                                   for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: camera),
              session.canAddInput(input) else { return false }

        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else { return false }

        session.beginConfiguration()
        session.addInput(input)
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        output.metadataObjectTypes = [.qr]
        session.commitConfiguration()

        isConfigured = true
        return true
    }

    nonisolated func metadataOutput(_ output: AVCaptureMetadataOutput,
                                    didOutput objects: [AVMetadataObject],
                                    from connection: AVCaptureConnection) {
        guard let code = objects.compactMap({ $0 as? AVMetadataMachineReadableCodeObject }).first,
              let value = code.stringValue else { return }
        MainActor.assumeIsolated {
            guard scannedCode == nil else { return }      // one scan, not sixty a second
            scannedCode = value
        }
    }
}

private struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.layer.session = session
        view.layer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ view: PreviewView, context: Context) {}

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        override var layer: AVCaptureVideoPreviewLayer {
            super.layer as! AVCaptureVideoPreviewLayer
        }
    }
}
