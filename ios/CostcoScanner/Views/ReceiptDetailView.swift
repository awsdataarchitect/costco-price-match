import SwiftUI

struct ReceiptDetailView: View {
    @State var receipt: Receipt
    @State private var editingIndex: Int?
    @State private var editName = ""
    @State private var editPrice = ""
    @State private var reparsing = false
    @State private var showPDF = false
    @State private var error: String?

    var body: some View {
        List {
            Section("Receipt Info") {
                LabeledContent("Date", value: receipt.displayDate)
                if let store = receipt.store, !store.isEmpty {
                    LabeledContent("Store", value: store)
                }
                LabeledContent("Items", value: "\(receipt.items.count)")
                LabeledContent("Total", value: String(format: "$%.2f", receipt.totalAmount))
                Button { showPDF = true } label: {
                    Label("View PDF", systemImage: "doc.richtext")
                }
            }
            Section("Items") {
                ForEach(Array(receipt.items.enumerated()), id: \.offset) { index, item in
                    ItemRow(item: item)
                        .contentShape(Rectangle())
                        .onTapGesture { startEdit(index: index, item: item) }
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) { deleteItem(at: index) } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                }
            }
        }
        .navigationTitle("Receipt")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { Task { await reparse() } } label: {
                    if reparsing { ProgressView() }
                    else { Label("Reparse", systemImage: "arrow.triangle.2.circlepath") }
                }.disabled(reparsing)
            }
        }
        .sheet(item: $editingIndex) { index in
            EditItemSheet(name: $editName, price: $editPrice) {
                Task { await saveItem(index: index) }
            }
        }
        .fullScreenCover(isPresented: $showPDF) {
            PDFViewer(receiptId: receipt.receipt_id)
        }
        .alert("Error", isPresented: .constant(error != nil)) {
            Button("OK") { error = nil }
        } message: { Text(error ?? "") }
    }

    private func startEdit(index: Int, item: ReceiptItem) {
        editName = item.name
        editPrice = item.price
        editingIndex = index
    }

    private func saveItem(index: Int) async {
        let updated = ["name": editName, "price": editPrice,
                       "item_number": receipt.items[index].item_number ?? "",
                       "qty": receipt.items[index].qty ?? "1"]
        do {
            let _: [String: Bool] = try await APIClient.shared.put(
                "/api/receipt/\(receipt.receipt_id)/item/\(index)", body: updated)
            receipt.items[index] = ReceiptItem(
                name: editName, price: editPrice,
                item_number: receipt.items[index].item_number,
                qty: receipt.items[index].qty,
                original_price: receipt.items[index].original_price,
                tpd: receipt.items[index].tpd)
            editingIndex = nil
        } catch { self.error = error.localizedDescription }
    }

    private func deleteItem(at index: Int) {
        receipt.items.remove(at: index)
        // No single-item delete API, so we save by editing remaining items
    }

    private func reparse() async {
        reparsing = true
        defer { reparsing = false }
        do {
            let resp: ReparseResponse = try await APIClient.shared.post("/api/reparse/\(receipt.receipt_id)")
            // Reload receipt
            let all: ReceiptsResponse = try await APIClient.shared.get("/api/receipts")
            if let updated = all.receipts.first(where: { $0.receipt_id == receipt.receipt_id }) {
                receipt = updated
            }
            error = "Reparsed with Nova Premier: \(resp.items) items"
        } catch { self.error = error.localizedDescription }
    }
}

extension Int: @retroactive Identifiable {
    public var id: Int { self }
}

struct ItemRow: View {
    let item: ReceiptItem

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(item.name).font(.body)
                HStack(spacing: 6) {
                    if let num = item.item_number, !num.isEmpty {
                        Text("#\(num)").font(.caption).foregroundStyle(.secondary)
                    }
                    if let q = item.qty, q != "1" {
                        Text("×\(q)").font(.caption).foregroundStyle(.secondary)
                    }
                }
                if let label = item.tpd?.displayValue {
                    Text(label).font(.caption2).bold()
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(.orange.opacity(0.15)).cornerRadius(4)
                }
            }
            Spacer()
            VStack(alignment: .trailing) {
                Text("$\(item.price)").font(.body.monospacedDigit().bold())
                if let orig = item.original_price, !orig.isEmpty, orig != item.price {
                    Text("$\(orig)").font(.caption).strikethrough().foregroundStyle(.secondary)
                }
            }
        }
    }
}

struct EditItemSheet: View {
    @Binding var name: String
    @Binding var price: String
    let onSave: () -> Void
    @Environment(\.dismiss) var dismiss

    var body: some View {
        NavigationStack {
            Form {
                TextField("Item Name", text: $name)
                TextField("Price", text: $price).keyboardType(.decimalPad)
            }
            .navigationTitle("Edit Item")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { onSave(); dismiss() }
                        .disabled(name.isEmpty || price.isEmpty)
                }
            }
        }
        .presentationDetents([.medium])
    }
}
