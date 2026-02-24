import SwiftUI

struct AnalysisView: View {
    @EnvironmentObject var config: BackendConfig
    @State private var receipts: [Receipt] = []
    @State private var selectedIds: Set<String> = []
    @State private var rawOutput = ""
    @State private var analyzing = false
    @State private var error: String?

    @State private var analysisTask: Task<Void, Never>?

    var body: some View {
        NavigationStack {
            if !config.isConnected {
                ConnectPrompt()
                    .navigationTitle("Analyze")
            } else {
            VStack(spacing: 0) {
                // Receipt selector
                VStack(spacing: 0) {
                    HStack {
                        Image(systemName: "doc.text.fill").foregroundStyle(.orange)
                        Text("Receipts to analyze").font(.subheadline.weight(.medium))
                        Spacer()
                        Text(selectedIds.isEmpty ? "All (\(receipts.count))" : "\(selectedIds.count) of \(receipts.count)")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                    .padding(.horizontal).padding(.top, 12).padding(.bottom, 8)

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ChipButton(label: "All", selected: selectedIds.isEmpty) {
                                selectedIds.removeAll()
                            }
                            ForEach(receipts) { r in
                                ChipButton(
                                    label: "\(r.displayDate) · $\(String(format: "%.0f", r.totalAmount))",
                                    selected: selectedIds.contains(r.receipt_id)
                                ) {
                                    if selectedIds.contains(r.receipt_id) { selectedIds.remove(r.receipt_id) }
                                    else { selectedIds.insert(r.receipt_id) }
                                }
                            }
                        }
                        .padding(.horizontal).padding(.bottom, 12)
                    }
                    Divider()
                }
                .background(.ultraThinMaterial)

                if analyzing {
                    HStack(spacing: 8) {
                        ProgressView()
                        Text("Analyzing with Nova AI...").font(.subheadline).foregroundStyle(.secondary)
                        Spacer()
                        Button("Stop") { analysisTask?.cancel(); analyzing = false }.font(.caption).buttonStyle(.bordered).tint(.red)
                    }.padding()
                }

                if rawOutput.isEmpty && !analyzing {
                    Spacer()
                    VStack(spacing: 12) {
                        Image(systemName: "chart.bar.doc.horizontal").font(.system(size: 48)).foregroundStyle(.tertiary)
                        Text("Tap Analyze to find price matches").font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                } else {
                    AnalysisResultView(markdown: rawOutput)
                }
            }
            .navigationTitle("Analysis")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        analysisTask = Task { await runAnalysis() }
                    } label: {
                        Image(systemName: "play.circle.fill").font(.title3).symbolRenderingMode(.hierarchical)
                    }.disabled(analyzing)
                }
                if !rawOutput.isEmpty {
                    ToolbarItem(placement: .topBarLeading) {
                        ShareLink(item: rawOutput) { Image(systemName: "square.and.arrow.up") }
                    }
                }
            }
            .alert("Error", isPresented: .constant(error != nil)) {
                Button("OK") { error = nil }
            } message: { Text(error ?? "") }
            .task { await loadReceipts() }
            } // else connected
        }
    }

    private func loadReceipts() async {
        do {
            let resp: ReceiptsResponse = try await APIClient.shared.get("/api/receipts")
            receipts = resp.receipts.sorted { ($0.receipt_date ?? "") > ($1.receipt_date ?? "") }
        } catch is CancellationError { }
          catch let e as URLError where e.code == .cancelled { }
          catch APIError.notConnected, APIError.noToken { }
          catch { self.error = error.localizedDescription }
    }

    private func runAnalysis() async {
        analyzing = true
        rawOutput = ""
        defer { analyzing = false }
        do {
            let ids = selectedIds.isEmpty ? nil : Array(selectedIds)
            let bytes = try await APIClient.shared.analyzeStream(receiptIds: ids)
            for try await line in bytes.lines {
                if Task.isCancelled { break }
                if line.hasPrefix("data: ") {
                    let payload = String(line.dropFirst(6))
                    if payload == "[DONE]" { break }
                    if let data = payload.data(using: .utf8),
                       let json = try? JSONSerialization.jsonObject(with: data) as? [String: String],
                       let type = json["type"], let text = json["text"] {
                        print("SSE: type=\(type) len=\(text.count)")
                        if type == "done" { await MainActor.run { rawOutput = text }; break }
                        else if type == "chunk" { await MainActor.run { rawOutput += text } }
                    }
                }
            }
        } catch is CancellationError { }
          catch let e as URLError where e.code == .cancelled { }
          catch APIError.notConnected, APIError.noToken { }
          catch {
            print("Analysis error: \(error)")
            self.error = error.localizedDescription
          }
    }
}

// MARK: - Parsed Result View

struct AnalysisResultView: View {
    let markdown: String

    private var sections: [AnalysisSection] { parseMarkdown(markdown) }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                ForEach(sections) { section in
                    if !section.title.isEmpty {
                        Text(cleanHeader(section.title))
                            .font(.title3.bold()).padding(.horizontal)
                    }
                    if !section.rows.isEmpty {
                        ForEach(section.rows) { row in
                            PriceMatchCard(row: row)
                        }
                    }
                    if !section.summaryItems.isEmpty {
                        SummaryCard(items: section.summaryItems)
                            .padding(.horizontal)
                    }
                    if !section.text.isEmpty {
                        Text(cleanText(section.text))
                            .font(.subheadline).foregroundStyle(.secondary)
                            .padding(.horizontal)
                    }
                }
            }
            .padding(.vertical)
        }
    }

    private func cleanHeader(_ s: String) -> String {
        s.replacingOccurrences(of: "##", with: "").replacingOccurrences(of: "# ", with: "").trimmingCharacters(in: .whitespaces)
    }

    private func cleanText(_ s: String) -> String {
        var t = s
        let linkPattern = /\[([^\]]+)\]\([^\)]+\)/
        t = t.replacing(linkPattern) { String($0.1) }
        t = t.replacingOccurrences(of: "**", with: "")
        return t.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

struct SummaryCard: View {
    let items: [(label: String, value: String, emoji: String)]

    var body: some View {
        VStack(spacing: 12) {
            ForEach(items, id: \.label) { item in
                HStack {
                    Text(item.emoji).font(.title2)
                    Text(item.label).font(.subheadline.weight(.medium))
                    Spacer()
                    Text(item.value)
                        .font(.title2.bold().monospacedDigit())
                        .foregroundStyle(item.value.contains("-") ? .red : .green)
                }
                if item.label != items.last?.label {
                    Divider()
                }
            }
        }
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 16)
                .fill(.ultraThinMaterial)
                .overlay(
                    RoundedRectangle(cornerRadius: 16)
                        .stroke(.orange.opacity(0.3), lineWidth: 1)
                )
        )
    }
}

struct PriceMatchCard: View {
    let row: TableRow
    @State private var showPDF = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(row.item).font(.subheadline.weight(.semibold)).lineLimit(2)
                Spacer()
                if !row.savings.isEmpty {
                    Text(row.savings).font(.subheadline.bold()).foregroundStyle(.green)
                        .padding(.horizontal, 8).padding(.vertical, 4)
                        .background(.green.opacity(0.1)).cornerRadius(8)
                }
            }
            HStack(spacing: 12) {
                if !row.itemNumber.isEmpty {
                    Label(row.itemNumber, systemImage: "number").font(.caption)
                }
                if !row.date.isEmpty {
                    Label(row.date, systemImage: "calendar").font(.caption)
                }
            }.foregroundStyle(.secondary)
            HStack(spacing: 16) {
                if !row.paid.isEmpty {
                    VStack { Text("Paid").font(.caption2).foregroundStyle(.secondary); Text(row.paid).font(.callout.monospacedDigit()) }
                }
                if !row.salePrice.isEmpty {
                    VStack { Text("Sale").font(.caption2).foregroundStyle(.secondary); Text(row.salePrice).font(.callout.monospacedDigit()).foregroundStyle(.orange) }
                }
                Spacer()
                if row.receiptId != nil {
                    Button { showPDF = true } label: {
                        Label("PDF", systemImage: "doc.richtext").font(.caption)
                    }.buttonStyle(.bordered).tint(.orange)
                }
                if !row.source.isEmpty {
                    if let url = row.sourceURL {
                        Link(destination: url) {
                            HStack(spacing: 3) {
                                Text(row.source).font(.caption2)
                                Image(systemName: "arrow.up.right").font(.system(size: 8))
                            }
                            .padding(.horizontal, 6).padding(.vertical, 3)
                            .background(.orange.opacity(0.1)).foregroundStyle(.orange).cornerRadius(4)
                        }
                    } else {
                        Text(row.source).font(.caption2).padding(.horizontal, 6).padding(.vertical, 3)
                            .background(.secondary.opacity(0.1)).cornerRadius(4)
                    }
                }
            }
        }
        .padding(12)
        .background(.background)
        .cornerRadius(12)
        .shadow(color: .black.opacity(0.06), radius: 4, y: 2)
        .padding(.horizontal)
        .fullScreenCover(isPresented: $showPDF) {
            if let rid = row.receiptId {
                PDFViewer(receiptId: rid)
            }
        }
    }
}

// MARK: - Markdown Parser

struct AnalysisSection: Identifiable {
    let id = UUID()
    var title: String = ""
    var rows: [TableRow] = []
    var text: String = ""
    var summaryItems: [(label: String, value: String, emoji: String)] = []
}

struct TableRow: Identifiable {
    let id = UUID()
    let item: String
    let itemNumber: String
    let date: String
    let paid: String
    let salePrice: String
    let savings: String
    let source: String
    var sourceURL: URL? = nil
    var receiptId: String? = nil
}

private func parseMarkdown(_ md: String) -> [AnalysisSection] {
    var sections: [AnalysisSection] = []
    var current = AnalysisSection()
    var inTable = false

    for line in md.components(separatedBy: "\n") {
        let trimmed = line.trimmingCharacters(in: .whitespaces)

        // Header
        if trimmed.hasPrefix("##") || trimmed.hasPrefix("# ") {
            if !current.title.isEmpty || !current.text.isEmpty || !current.rows.isEmpty {
                sections.append(current)
                current = AnalysisSection()
            }
            current.title = trimmed
            inTable = false
            continue
        }

        // Table header row
        if trimmed.contains("|") && !trimmed.hasPrefix("|--") && !trimmed.hasPrefix("|-") {
            let cols = trimmed.split(separator: "|").map { $0.trimmingCharacters(in: .whitespaces) }
            if !inTable && cols.count >= 3 {
                // Check if this is a header row (contains words like Item, Date, etc.)
                let isHeader = cols.contains(where: { $0.lowercased().contains("item") || $0.lowercased().contains("date") || $0.lowercased().contains("paid") })
                if isHeader {
                    _ = cols
                    inTable = true
                    continue
                }
            }
            // Data row
            if inTable && cols.count >= 3 {
                let get: (Int) -> String = { i in i < cols.count ? cols[i] : "" }
                // Clean markdown links from cell values
                let cleanCell: (String) -> String = { cell in
                    var c = cell
                    let linkPattern = /\[([^\]]+)\]\([^\)]+\)/
                    c = c.replacing(linkPattern) { String($0.1) }
                    return c
                }
                // Extract receipt ID from markdown link like [NAME](/api/receipt/UUID/pdf)
                let extractReceiptId: (String) -> String? = { cell in
                    if let match = cell.firstMatch(of: /\/api\/receipt\/([a-f0-9\-]+)\/pdf/) {
                        return String(match.1)
                    }
                    return nil
                }
                // Extract URL from markdown link like [text](https://...)
                let extractURL: (String) -> URL? = { cell in
                    if let match = cell.firstMatch(of: /\[([^\]]+)\]\((https?:\/\/[^\)]+)\)/) {
                        return URL(string: String(match.2))
                    }
                    return nil
                }
                let rid = extractReceiptId(get(0))
                let srcURL = extractURL(get(6))
                current.rows.append(TableRow(
                    item: cleanCell(get(0)),
                    itemNumber: cleanCell(get(1)),
                    date: get(2),
                    paid: get(3),
                    salePrice: get(4),
                    savings: get(5),
                    source: cleanCell(get(6)),
                    sourceURL: srcURL,
                    receiptId: rid
                ))
                continue
            }
        }

        // Separator row
        if trimmed.hasPrefix("|--") || trimmed.hasPrefix("|-") || (trimmed.hasPrefix("|") && trimmed.contains("---")) {
            continue
        }

        // Regular text
        if !trimmed.isEmpty {
            inTable = false
            // Strip bold markers
            let clean = trimmed.replacingOccurrences(of: "**", with: "")
            // Detect summary lines like "Potential Savings: $X.XX" or "💰 Already Saved: $X"
            let summaryPattern = /(?:\*\*)?([^*$]+?):\s*(\$[\d,.]+)(?:\*\*)?/
            if clean.contains("$"), let match = clean.firstMatch(of: summaryPattern) {
                let label = String(match.1).trimmingCharacters(in: .whitespaces)
                    .replacingOccurrences(of: "**", with: "")
                let value = String(match.2)
                // Pick emoji based on content
                let emoji: String
                if label.lowercased().contains("already") || label.lowercased().contains("tpd") { emoji = "✅" }
                else if label.lowercased().contains("potential") || label.lowercased().contains("saving") { emoji = "💰" }
                else if label.lowercased().contains("total") { emoji = "🎯" }
                else { emoji = "📊" }
                current.summaryItems.append((label: label, value: value, emoji: emoji))
            } else {
                if !current.text.isEmpty { current.text += "\n" }
                current.text += clean
            }
        }
    }
    if !current.title.isEmpty || !current.text.isEmpty || !current.rows.isEmpty {
        sections.append(current)
    }
    return sections
}

// MARK: - Chip Button

struct ChipButton: View {
    let label: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(.caption.weight(selected ? .semibold : .regular))
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(selected ? Color.orange : Color.secondary.opacity(0.12))
                .foregroundStyle(selected ? .white : .primary)
                .cornerRadius(20)
        }
        .buttonStyle(.plain)
        .sensoryFeedback(.selection, trigger: selected)
    }
}
