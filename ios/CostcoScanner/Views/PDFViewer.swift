import PDFKit
import SwiftUI

struct PDFViewer: View {
    let receiptId: String
    @State private var pdfData: Data?
    @State private var loading = true
    @State private var error: String?
    @Environment(\.dismiss) var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if loading {
                    ProgressView("Loading PDF...")
                } else if let data = pdfData {
                    PDFKitView(data: data)
                } else {
                    ContentUnavailableView("PDF Not Found", systemImage: "doc.questionmark")
                }
            }
            .navigationTitle("Receipt PDF")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
                if let data = pdfData {
                    ToolbarItem(placement: .topBarTrailing) {
                        ShareLink(item: data, preview: SharePreview("Receipt.pdf"))
                    }
                }
            }
            .alert("Error", isPresented: .constant(error != nil)) {
                Button("OK") { error = nil }
            } message: { Text(error ?? "") }
            .task { await loadPDF() }
        }
    }

    private func loadPDF() async {
        defer { loading = false }
        do {
            pdfData = try await APIClient.shared.getRaw("/api/receipt/\(receiptId)/pdf")
        } catch { self.error = error.localizedDescription }
    }
}

struct PDFKitView: UIViewRepresentable {
    let data: Data
    func makeUIView(context: Context) -> PDFView {
        let v = PDFView()
        v.autoScales = true
        v.document = PDFDocument(data: data)
        return v
    }
    func updateUIView(_ v: PDFView, context: Context) {}
}
