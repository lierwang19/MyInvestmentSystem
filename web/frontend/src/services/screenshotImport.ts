/**
 * Screenshot Import Service — 截图识别导入交易
 */
import api from '../api'

export interface ParsedTransaction {
  row_index: number
  trans_type: string
  stock_name: string
  ts_code: string | null
  matched_name: string | null
  match_status: 'matched' | 'unmatched' | 'ambiguous' | 'cache_unavailable'
  duplicate_status: 'new' | 'maybe_duplicate' | 'duplicate' | 'unknown'
  candidates?: { ts_code: string; name: string; industry: string }[]
  ocr_price: number
  ocr_quantity: number
  ocr_total: number
  api_price: number
  api_amount: number
  date: string
  time: string
}

export interface ParseSummary {
  total: number
  matched: number
  unmatched: number
  ambiguous: number
  duplicates: number
  errors: number
}

export interface ImportTransactionItem {
  ts_code: string
  name: string
  trans_type: string
  price: number
  amount: number
  date: string
  time: string
}

export const screenshotImportService = {
  parse(file: File | Blob, year?: number, accountId?: string) {
    const formData = new FormData()
    formData.append('file', file)
    if (year != null) formData.append('year', String(year))
    if (accountId) formData.append('account_id', accountId)
    return api.post<{ status: string; data: ParsedTransaction[]; summary: ParseSummary }>(
      '/api/screenshot/parse',
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    )
  },

  importTrades(accountId: string, transactions: ImportTransactionItem[]) {
    return api.post(`/api/screenshot/import/${accountId}`, { transactions })
  },
}
