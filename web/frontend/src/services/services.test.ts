/**
 * Unit tests for API service modules.
 * These test that service functions correctly delegate to the axios instance.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock the api module
vi.mock('../api', () => {
    const mockApi = {
        get: vi.fn().mockResolvedValue({ data: { status: 'success' } }),
        post: vi.fn().mockResolvedValue({ data: { status: 'success' } }),
        put: vi.fn().mockResolvedValue({ data: { status: 'success' } }),
        delete: vi.fn().mockResolvedValue({ data: { status: 'success' } }),
    }
    return { default: mockApi }
})

import api from '../api'
import { marketService } from '../services/market'
import { accountsService } from '../services/accounts'
import { systemService } from '../services/system'
import { transactionsService } from '../services/transactions'

describe('marketService', () => {
    beforeEach(() => vi.clearAllMocks())

    it('getCurrentStatus calls GET /api/market/current', async () => {
        await marketService.getCurrentStatus()
        expect(api.get).toHaveBeenCalledWith('/api/market/current')
    })

    it('getMonthlyHistory passes years param', async () => {
        await marketService.getMonthlyHistory(3)
        expect(api.get).toHaveBeenCalledWith('/api/market/monthly?years=3')
    })

    it('searchStocks passes query', async () => {
        await marketService.searchStocks('平安')
        expect(api.get).toHaveBeenCalledWith('/api/market/search?q=平安')
    })
})

describe('accountsService', () => {
    beforeEach(() => vi.clearAllMocks())

    it('getAll calls GET /api/accounts-db', async () => {
        await accountsService.getAll()
        expect(api.get).toHaveBeenCalledWith('/api/accounts-db')
    })

    it('create calls POST with name', async () => {
        await accountsService.create('Test Account', 1)
        expect(api.post).toHaveBeenCalledWith('/api/accounts-db', { name: 'Test Account', broker_id: 1 })
    })

    it('delete calls DELETE with id', async () => {
        await accountsService.delete('abc123')
        expect(api.delete).toHaveBeenCalledWith('/api/accounts-db/abc123')
    })
})

describe('systemService', () => {
    beforeEach(() => vi.clearAllMocks())

    it('refreshMarketData calls POST /api/system/refresh-market', async () => {
        await systemService.refreshMarketData()
        expect(api.post).toHaveBeenCalledWith('/api/system/refresh-market')
    })

    it('getDataHealth calls GET /api/system/data-health', async () => {
        await systemService.getDataHealth()
        expect(api.get).toHaveBeenCalledWith('/api/system/data-health')
    })
})

describe('transactionsService', () => {
    beforeEach(() => vi.clearAllMocks())

    it('getByAccount calls GET with account_id path', async () => {
        await transactionsService.getByAccount('acc1')
        expect(api.get).toHaveBeenCalledWith('/api/transactions/acc1', { params: {} })
    })

    it('deleteById calls DELETE with id', async () => {
        await transactionsService.deleteById(42)
        expect(api.delete).toHaveBeenCalledWith('/api/transactions/42')
    })
})
