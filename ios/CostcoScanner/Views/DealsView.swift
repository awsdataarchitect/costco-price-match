import SwiftUI

// MARK: - Date Filter

enum DateFilter: String, CaseIterable {
    case all = "All"
    case activeNow = "Active Now"
    case expiringSoon = "Expiring Soon"
    case expired = "Expired"
    case custom = "Custom Range"

    var icon: String {
        switch self {
        case .all: "calendar"
        case .activeNow: "flame.fill"
        case .expiringSoon: "clock.badge.exclamationmark"
        case .expired: "clock.badge.xmark"
        case .custom: "calendar.badge.clock"
        }
    }
}

// MARK: - DealsView

struct DealsView: View {
    @EnvironmentObject var config: BackendConfig
    @State private var deals: [PriceDrop] = []
    @State private var loading = false
    @State private var scanning = false
    @State private var error: String?
    @State private var searchText = ""
    @State private var selectedSource: String?
    @State private var dateFilter: DateFilter = .all
    @State private var customStart = Calendar.current.date(byAdding: .month, value: -1, to: Date())!
    @State private var customEnd = Date()
    @State private var showClearConfirm = false
    @State private var showDateDeleteConfirm = false

    private static let dateFmt: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; return f
    }()

    private var sources: [String] {
        Array(Set(deals.compactMap { $0.source }.filter { !$0.isEmpty })).sorted()
    }

    private var filtered: [PriceDrop] {
        deals.filter { deal in
            let matchesSearch = searchText.isEmpty || deal.item_name.localizedCaseInsensitiveContains(searchText)
            let matchesSource = selectedSource == nil || deal.source == selectedSource
            let matchesDate = passesDateFilter(deal)
            return matchesSearch && matchesSource && matchesDate
        }
    }

    private func parseDate(_ s: String?) -> Date? {
        guard let s, !s.isEmpty else { return nil }
        return Self.dateFmt.date(from: String(s.prefix(10)))
    }

    private func passesDateFilter(_ deal: PriceDrop) -> Bool {
        let today = Calendar.current.startOfDay(for: Date())
        switch dateFilter {
        case .all: return true
        case .activeNow:
            let start = parseDate(deal.promo_start) ?? .distantPast
            let end = parseDate(deal.promo_end) ?? .distantFuture
            return start <= today && today <= end
        case .expiringSoon:
            guard let end = parseDate(deal.promo_end) else { return false }
            let daysLeft = Calendar.current.dateComponents([.day], from: today, to: end).day ?? 999
            return daysLeft >= 0 && daysLeft <= 7
        case .expired:
            guard let end = parseDate(deal.promo_end) else { return false }
            return end < today
        case .custom:
            let start = Calendar.current.startOfDay(for: customStart)
            let end = Calendar.current.startOfDay(for: customEnd)
            // Match if promo period overlaps with custom range
            let promoStart = parseDate(deal.promo_start) ?? parseDate(deal.scanned_date) ?? .distantPast
            let promoEnd = parseDate(deal.promo_end) ?? promoStart
            return promoStart <= end && promoEnd >= start
        }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                filterBar
                content
            }
            .searchable(text: $searchText, prompt: "Search deals")
            .navigationTitle("Deals")
            .toolbar { toolbarMenu }
            .confirmationDialog("Delete all deals?", isPresented: $showClearConfirm, titleVisibility: .visible) {
                Button("Delete All", role: .destructive) { Task { await clearDeals() } }
            }
            .confirmationDialog("Delete \(filtered.count) filtered deals?", isPresented: $showDateDeleteConfirm, titleVisibility: .visible) {
                Button("Delete \(filtered.count) Deals", role: .destructive) { Task { await deleteFiltered() } }
            }
            .refreshable { await loadDeals() }
            .alert("Error", isPresented: .constant(error != nil)) {
                Button("OK") { error = nil }
            } message: { Text(error ?? "") }
            .task { await loadDeals() }
        }
    }

    // MARK: - Filter Bar

    private var filterBar: some View {
        VStack(spacing: 0) {
            // Source chips
            if !sources.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        SourceChip(label: "All", count: deals.count, selected: selectedSource == nil) {
                            selectedSource = nil
                        }
                        ForEach(sources, id: \.self) { src in
                            SourceChip(label: src, count: deals.filter { $0.source == src }.count, selected: selectedSource == src) {
                                selectedSource = selectedSource == src ? nil : src
                            }
                        }
                    }
                    .padding(.horizontal).padding(.vertical, 8)
                }
            }

            // Date filter chips
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(DateFilter.allCases, id: \.self) { filter in
                        DateChip(filter: filter, selected: dateFilter == filter, count: countForDateFilter(filter)) {
                            withAnimation(.snappy(duration: 0.25)) {
                                dateFilter = dateFilter == filter ? .all : filter
                            }
                        }
                    }
                }
                .padding(.horizontal).padding(.vertical, 8)
            }

            // Custom date range picker
            if dateFilter == .custom {
                HStack(spacing: 12) {
                    DatePicker("From", selection: $customStart, displayedComponents: .date)
                        .labelsHidden()
                    Image(systemName: "arrow.right").foregroundStyle(.secondary).font(.caption)
                    DatePicker("To", selection: $customEnd, displayedComponents: .date)
                        .labelsHidden()
                }
                .padding(.horizontal).padding(.bottom, 8)
                .transition(.move(edge: .top).combined(with: .opacity))
            }

            Divider()

            // Results summary bar
            HStack {
                Text("\(filtered.count) deals")
                    .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                Spacer()
                if dateFilter != .all || selectedSource != nil {
                    Button { withAnimation { dateFilter = .all; selectedSource = nil } } label: {
                        Label("Clear Filters", systemImage: "xmark.circle.fill")
                            .font(.caption).foregroundStyle(.orange)
                    }
                }
            }
            .padding(.horizontal).padding(.vertical, 6)
        }
    }

    private func countForDateFilter(_ filter: DateFilter) -> Int {
        deals.filter { deal in
            let matchesSource = selectedSource == nil || deal.source == selectedSource
            guard matchesSource else { return false }
            let today = Calendar.current.startOfDay(for: Date())
            switch filter {
            case .all: return true
            case .activeNow:
                let start = parseDate(deal.promo_start) ?? .distantPast
                let end = parseDate(deal.promo_end) ?? .distantFuture
                return start <= today && today <= end
            case .expiringSoon:
                guard let end = parseDate(deal.promo_end) else { return false }
                let daysLeft = Calendar.current.dateComponents([.day], from: today, to: end).day ?? 999
                return daysLeft >= 0 && daysLeft <= 7
            case .expired:
                guard let end = parseDate(deal.promo_end) else { return false }
                return end < today
            case .custom:
                let start = Calendar.current.startOfDay(for: customStart)
                let end = Calendar.current.startOfDay(for: customEnd)
                let promoStart = parseDate(deal.promo_start) ?? parseDate(deal.scanned_date) ?? .distantPast
                let promoEnd = parseDate(deal.promo_end) ?? promoStart
                return promoStart <= end && promoEnd >= start
            }
        }.count
    }

    // MARK: - Content

    @ViewBuilder
    private var content: some View {
        if !config.isConnected {
            ConnectPrompt()
        } else if loading && deals.isEmpty {
            Spacer()
            ProgressView("Loading deals...")
            Spacer()
        } else if deals.isEmpty {
            Spacer()
            ContentUnavailableView("No Deals", systemImage: "tag", description: Text("Scan for current Costco price drops"))
            Spacer()
        } else if filtered.isEmpty {
            Spacer()
            ContentUnavailableView.search
            Spacer()
        } else {
            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach(filtered) { deal in
                        DealCard(deal: deal, onDelete: { deleteDeal(deal) })
                    }
                }
                .padding(.horizontal).padding(.vertical, 8)
            }
        }
    }

    // MARK: - Toolbar

    private var toolbarMenu: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                Button { Task { await scanDeals() } } label: {
                    Label("Scan New Deals", systemImage: "arrow.clockwise")
                }
                Divider()
                if dateFilter != .all || selectedSource != nil {
                    Button(role: .destructive) { showDateDeleteConfirm = true } label: {
                        Label("Delete Filtered (\(filtered.count))", systemImage: "trash")
                    }
                }
                if let src = selectedSource {
                    Button(role: .destructive) { Task { await deleteBySource(src) } } label: {
                        Label("Delete \(src)", systemImage: "trash")
                    }
                }
                if !deals.isEmpty {
                    Button(role: .destructive) { showClearConfirm = true } label: {
                        Label("Delete All Deals", systemImage: "trash.fill")
                    }
                }
            } label: {
                if scanning { ProgressView() }
                else { Image(systemName: "ellipsis.circle") }
            }.disabled(scanning)
        }
    }

    // MARK: - Actions

    private func loadDeals() async {
        loading = true
        defer { loading = false }
        do {
            let resp: PriceDropsListResponse = try await APIClient.shared.get("/api/price-drops")
            deals = resp.price_drops
        } catch is CancellationError { }
          catch let e as URLError where e.code == .cancelled { }
          catch APIError.notConnected, APIError.noToken { }
          catch { self.error = error.localizedDescription }
    }

    private func scanDeals() async {
        scanning = true
        defer { scanning = false }
        do {
            let resp: ScanResponse = try await APIClient.shared.post("/api/scan-prices?force_refresh=true")
            deals = resp.items
            selectedSource = nil; dateFilter = .all
        } catch { self.error = error.localizedDescription }
    }

    private func deleteDeal(_ deal: PriceDrop) {
        withAnimation { deals.removeAll { $0.id == deal.id } }
        Task { try? await APIClient.shared.delete("/api/price-drop/\(deal.item_id)") }
    }

    private func deleteBySource(_ source: String) async {
        let toDelete = deals.filter { $0.source == source }
        withAnimation { deals.removeAll { $0.source == source }; selectedSource = nil }
        for deal in toDelete { try? await APIClient.shared.delete("/api/price-drop/\(deal.item_id)") }
    }

    private func deleteFiltered() async {
        let toDelete = filtered
        let ids = Set(toDelete.map(\.id))
        withAnimation { deals.removeAll { ids.contains($0.id) } }
        dateFilter = .all; selectedSource = nil
        for deal in toDelete { try? await APIClient.shared.delete("/api/price-drop/\(deal.item_id)") }
    }

    private func clearDeals() async {
        do {
            try await APIClient.shared.delete("/api/price-drops")
            withAnimation { deals = []; selectedSource = nil; dateFilter = .all }
        } catch { self.error = error.localizedDescription }
    }
}

// MARK: - Date Chip

struct DateChip: View {
    let filter: DateFilter
    let selected: Bool
    let count: Int
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: filter.icon).font(.caption2)
                Text(filter.rawValue).font(.caption.weight(selected ? .semibold : .regular))
                if filter != .all {
                    Text("\(count)").font(.caption2.monospacedDigit())
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(selected ? Color.white.opacity(0.3) : Color.secondary.opacity(0.15))
                        .cornerRadius(8)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 7)
            .background(selected ? Color.orange : Color(.systemGray6))
            .foregroundStyle(selected ? .white : .primary)
            .cornerRadius(20)
        }
        .buttonStyle(.plain)
        .sensoryFeedback(.selection, trigger: selected)
    }
}

// MARK: - Source Chip

struct SourceChip: View {
    let label: String
    let count: Int
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Text(label).font(.caption.weight(selected ? .semibold : .regular))
                Text("\(count)").font(.caption2.monospacedDigit())
                    .padding(.horizontal, 5).padding(.vertical, 1)
                    .background(selected ? Color.white.opacity(0.3) : Color.secondary.opacity(0.15))
                    .cornerRadius(8)
            }
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(selected ? Color.blue : Color(.systemGray6))
            .foregroundStyle(selected ? .white : .primary)
            .cornerRadius(20)
        }
        .buttonStyle(.plain)
        .sensoryFeedback(.selection, trigger: selected)
    }
}

// MARK: - Deal Card

struct DealCard: View {
    let deal: PriceDrop
    let onDelete: () -> Void

    static let parseDateFmt: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; return f
    }()

    private static let shortDate: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMM d"; return f
    }()

    private var daysLeft: Int? {
        guard let endStr = deal.promo_end, !endStr.isEmpty,
              let end = DealCard.parseDateFmt.date(from: String(endStr.prefix(10))) else { return nil }
        return Calendar.current.dateComponents([.day], from: Calendar.current.startOfDay(for: Date()), to: end).day
    }

    private var urgencyColor: Color {
        guard let d = daysLeft else { return .clear }
        if d < 0 { return .gray }
        if d <= 3 { return .red }
        if d <= 7 { return .orange }
        return .green
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Header: name + source badge
            HStack(alignment: .top) {
                if let link = deal.link, let url = URL(string: link) {
                    Link(destination: url) {
                        HStack(alignment: .firstTextBaseline, spacing: 4) {
                            Text(deal.item_name).font(.subheadline.weight(.semibold))
                                .foregroundStyle(.primary).multilineTextAlignment(.leading)
                            Image(systemName: "arrow.up.right").font(.caption2).foregroundStyle(.orange)
                        }
                    }
                } else {
                    Text(deal.item_name).font(.subheadline.weight(.semibold))
                }
                Spacer(minLength: 8)
                if let src = deal.source, !src.isEmpty {
                    Text(src).font(.caption2.weight(.medium))
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(.blue.opacity(0.12)).foregroundStyle(.blue)
                        .cornerRadius(6)
                }
            }

            // Price row
            HStack(spacing: 12) {
                if let sale = deal.sale_price, !sale.isEmpty {
                    HStack(spacing: 4) {
                        Image(systemName: "tag.fill").font(.caption2)
                        Text("$\(sale)").font(.callout.bold())
                    }.foregroundStyle(.green)
                }
                if let orig = deal.original_price, !orig.isEmpty {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.down.circle.fill").font(.caption2)
                        Text("$\(orig) off").font(.callout.bold())
                    }.foregroundStyle(.red)
                }
                Spacer()
            }

            // Date row + urgency
            HStack(spacing: 8) {
                if let start = deal.promo_start, !start.isEmpty {
                    Label(formatShort(start), systemImage: "calendar")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                if let end = deal.promo_end, !end.isEmpty {
                    HStack(spacing: 2) {
                        Image(systemName: "arrow.right").font(.system(size: 8))
                        Text(formatShort(end))
                    }.font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                if let d = daysLeft {
                    Text(d < 0 ? "Expired" : d == 0 ? "Last day!" : "\(d)d left")
                        .font(.caption2.weight(.bold))
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(urgencyColor.opacity(0.15))
                        .foregroundStyle(urgencyColor)
                        .cornerRadius(6)
                }
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 14)
                .fill(Color(.secondarySystemGroupedBackground))
                .shadow(color: .black.opacity(0.06), radius: 4, y: 2)
        )
        .contextMenu {
            if let link = deal.link, let url = URL(string: link) {
                Link(destination: url) { Label("Open Link", systemImage: "safari") }
            }
            Button(role: .destructive, action: onDelete) { Label("Delete", systemImage: "trash") }
        }
    }

    private func formatShort(_ s: String) -> String {
        guard let d = DealCard.parseDateFmt.date(from: String(s.prefix(10))) else { return s }
        return Self.shortDate.string(from: d)
    }
}


