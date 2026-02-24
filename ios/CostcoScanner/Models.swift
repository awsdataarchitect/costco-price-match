import Foundation

// MARK: - Receipt
struct Receipt: Codable, Identifiable {
    let receipt_id: String
    var items: [ReceiptItem]
    var receipt_date: String?
    var store: String?
    var upload_date: String?
    var id: String { receipt_id }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        receipt_id = try c.decode(String.self, forKey: .receipt_id)
        items = (try? c.decode([ReceiptItem].self, forKey: .items)) ?? []
        receipt_date = try? c.decode(String.self, forKey: .receipt_date)
        store = try? c.decode(String.self, forKey: .store)
        upload_date = try? c.decode(String.self, forKey: .upload_date)
    }

    var displayDate: String {
        receipt_date ?? upload_date?.prefix(10).description ?? "Unknown"
    }

    var totalAmount: Double {
        (items).compactMap { Double($0.price) }.reduce(0, +)
    }
}

struct ReceiptItem: Codable, Identifiable {
    let name: String
    let price: String
    var item_number: String?
    var qty: String?
    var original_price: String?
    var tpd: TPD?
    var id: String { "\(name)-\(price)-\(item_number ?? "")" }
}

// tpd can be bool or string depending on receipt
enum TPD: Codable {
    case bool(Bool), string(String)
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else { self = .bool(false) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .bool(let b): try c.encode(b)
        case .string(let s): try c.encode(s)
        }
    }
    var displayValue: String? {
        switch self {
        case .bool(let b): b ? "TPD" : nil
        case .string(let s): s.isEmpty ? nil : s
        }
    }
}

// MARK: - Price Drop
struct PriceDrop: Codable, Identifiable {
    let item_id: String
    let item_name: String
    var sale_price: String?
    var original_price: String?
    var item_number: String?
    var source: String?
    var link: String?
    var promo_start: String?
    var promo_end: String?
    var scanned_date: String?
    var id: String { item_id }
}

// MARK: - API Responses
struct ReceiptsResponse: Codable { let receipts: [Receipt] }
struct PriceDropsListResponse: Codable { let price_drops: [PriceDrop] }
struct ScanResponse: Codable { let price_drops: Int; let items: [PriceDrop] }
struct UploadResponse: Codable { let receipt: Receipt; let parsed_items: Int }
struct MessageResponse: Codable { let message: String }
struct ReparseResponse: Codable { let items: Int; let model: String }
