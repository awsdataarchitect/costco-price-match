import SwiftUI
import UniformTypeIdentifiers

struct ReceiptsView: View {
    @EnvironmentObject var config: BackendConfig
    @State private var receipts: [Receipt] = []
    @State private var loading = false
    @State private var showFilePicker = false
    @State private var showClearConfirm = false
    @State private var uploading = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Group {
                if !config.isConnected {
                    ConnectPrompt()
                } else if loading && receipts.isEmpty {
                    VStack(spacing: 12) {
                        ProgressView().scaleEffect(1.2)
                        Text("Loading receipts...").font(.subheadline)
                        Text("First load may take a moment").font(.caption).foregroundStyle(.secondary)
                    }
                } else if receipts.isEmpty {
                    ContentUnavailableView("No Receipts", systemImage: "doc.text",
                        description: Text("Upload a Costco receipt PDF to get started"))
                } else {
                    List {
                        ForEach(receipts) { receipt in
                            NavigationLink(destination: ReceiptDetailView(receipt: receipt)) {
                                ReceiptRow(receipt: receipt)
                            }
                        }
                        .onDelete(perform: deleteReceipts)
                    }
                }
            }
            .navigationTitle("Receipts (\(receipts.count))")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button { showFilePicker = true } label: {
                            Label("Upload PDF", systemImage: "doc.badge.plus")
                        }
                        if !receipts.isEmpty {
                            Button(role: .destructive) { showClearConfirm = true } label: {
                                Label("Clear All", systemImage: "trash")
                            }
                        }
                    } label: {
                        if uploading { ProgressView() }
                        else { Image(systemName: "plus") }
                    }
                }
            }
            .overlay {
                if uploading {
                    VStack(spacing: 12) {
                        ProgressView()
                        Text("Uploading & parsing...").font(.caption).foregroundStyle(.secondary)
                    }
                    .padding(24).background(.ultraThinMaterial).cornerRadius(16)
                }
            }
            .confirmationDialog("Delete all receipts?", isPresented: $showClearConfirm, titleVisibility: .visible) {
                Button("Delete All", role: .destructive) { Task { await clearAll() } }
            }
            .fileImporter(isPresented: $showFilePicker, allowedContentTypes: [UTType.pdf], allowsMultipleSelection: true) { result in
                Task { await handleFiles(result) }
            }
            .refreshable { await loadReceipts() }
            .alert("Error", isPresented: .constant(error != nil)) {
                Button("OK") { error = nil }
            } message: { Text(error ?? "") }
            .task { await loadReceipts() }
        }
    }

    private func loadReceipts() async {
        loading = true
        defer { loading = false }
        for attempt in 1...3 {
            do {
                let resp: ReceiptsResponse = try await APIClient.shared.get("/api/receipts")
                receipts = resp.receipts.sorted { ($0.receipt_date ?? "") > ($1.receipt_date ?? "") }
                return
            } catch is CancellationError {
                return
            } catch let urlError as URLError where urlError.code == .cancelled {
                return
            } catch APIError.notConnected, APIError.noToken {
                return
            } catch {
                if attempt == 3 { self.error = error.localizedDescription }
                else { try? await Task.sleep(for: .seconds(2)) }
            }
        }
    }

    private func handleFiles(_ result: Result<[URL], Error>) async {
        guard let urls = try? result.get() else { return }
        uploading = true
        defer { uploading = false }
        for url in urls {
            guard url.startAccessingSecurityScopedResource() else { continue }
            defer { url.stopAccessingSecurityScopedResource() }
            do {
                let data = try Data(contentsOf: url)
                let resp = try await APIClient.shared.upload(pdf: data, filename: url.lastPathComponent)
                receipts.insert(resp.receipt, at: 0)
            } catch { self.error = error.localizedDescription }
        }
    }

    private func deleteReceipts(at offsets: IndexSet) {
        let toDelete = offsets.map { receipts[$0] }
        receipts.remove(atOffsets: offsets)
        Task { for r in toDelete { try? await APIClient.shared.delete("/api/receipt/\(r.receipt_id)") } }
    }

    private func clearAll() async {
        do {
            try await APIClient.shared.delete("/api/receipts")
            receipts = []
        } catch { self.error = error.localizedDescription }
    }
}

struct ReceiptRow: View {
    let receipt: Receipt

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(receipt.displayDate).font(.headline)
                Spacer()
                Text(String(format: "$%.2f", receipt.totalAmount))
                    .font(.headline.monospacedDigit()).foregroundStyle(.orange)
            }
            HStack {
                if let store = receipt.store, !store.isEmpty {
                    Text(store).font(.caption).foregroundStyle(.secondary)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(.secondary.opacity(0.1)).cornerRadius(4)
                }
                Spacer()
                Text("\(receipt.items.count) items").font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }
}
