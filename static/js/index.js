window.app = Vue.createApp({
  el: '#vue',
  mixins: [windowMixin],
  data() {
    return {
      protocol: window.location.protocol,
      location: window.location.hostname,
      wslocation: window.location.hostname,
      filter: '',
      currency: 'USD',
      lnurlValue: '',
      websocketMessage: '',
      bitcoinswitches: [],
      taprootAssetsAvailable: false,
      availableAssets: [],
      loadingAssets: false,
      bitcoinswitchTable: {
        columns: [
          {
            name: 'title',
            align: 'left',
            label: 'title',
            field: 'title'
          },
          {
            name: 'wallet',
            align: 'left',
            label: 'wallet',
            field: 'wallet'
          },
          {
            name: 'currency',
            align: 'left',
            label: 'currency',
            field: 'currency'
          },
          {
            name: 'key',
            align: 'left',
            label: 'key',
            field: 'key'
          }
        ],
        pagination: {
          rowsPerPage: 10
        }
      },
      settingsDialog: {
        show: false,
        data: {}
      },
      formDialog: {
        show: false,
        data: {
          switches: [],
          lnurl_toggle: false,
          show_message: false,
          show_ack: false,
          show_price: 'None',
          device: 'pos',
          profit: 1,
          amount: 1,
          title: '',
          wallet: '',
          currency: 'sat',
          password: '',
          default_accepts_assets: false
        }
      },
      qrCodeDialog: {
        show: false,
        data: null
      }
    }
  },
  computed: {
    wsMessage() {
      return this.websocketMessage
    },
    currentSwitch() {
      if (!this.qrCodeDialog.data || !this.lnurlValue) return null
      return this.qrCodeDialog.data.switches.find(s => s.lnurl === this.lnurlValue)
    }
  },
  methods: {
    openQrCodeDialog(bitcoinswitchId) {
      const bitcoinswitch = _.findWhere(this.bitcoinswitches, {
        id: bitcoinswitchId
      })
      this.qrCodeDialog.data = _.clone(bitcoinswitch)
      this.qrCodeDialog.data.url =
        window.location.protocol + '//' + window.location.host
      this.lnurlValue = this.qrCodeDialog.data.switches[0].lnurl
      this.websocketConnector(
        'wss://' + window.location.host + '/api/v1/ws/' + bitcoinswitchId
      )
      this.qrCodeDialog.show = true
    },
    addSwitch() {
      this.formDialog.data.switches.push({
        amount: 10,
        pin: 0,
        duration: 1000,
        variable: false,
        comment: false,
        accepts_assets: this.formDialog.data.default_accepts_assets || false,
        accepted_asset_ids: []
      })
    },
    removeSwitch() {
      this.formDialog.data.switches.pop()
    },
    cancelFormDialog() {
      this.formDialog.show = false
      this.clearFormDialog()
    },
    closeFormDialog() {
      this.clearFormDialog()
    },
    sendFormData() {
      if (this.formDialog.data.id) {
        this.updateBitcoinswitch(
          this.g.user.wallets[0].adminkey,
          this.formDialog.data
        )
      } else {
        this.createBitcoinswitch(
          this.g.user.wallets[0].adminkey,
          this.formDialog.data
        )
      }
    },

    createBitcoinswitch(wallet, data) {
      const updatedData = {}
      for (const property in data) {
        if (data[property] !== undefined && data[property] !== null && data[property] !== '') {
          updatedData[property] = data[property]
        }
      }
      // Ensure boolean fields are included even if false
      updatedData.default_accepts_assets = data.default_accepts_assets || false
      
      LNbits.api
        .request(
          'POST',
          '/bitcoinswitch/api/v1/bitcoinswitch',
          wallet,
          updatedData
        )
        .then(response => {
          this.bitcoinswitches.push(response.data)
          this.formDialog.show = false
          this.clearFormDialog()
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    updateBitcoinswitch(wallet, data) {
      const updatedData = {}
      for (const property in data) {
        if (data[property] !== undefined && data[property] !== null && data[property] !== '') {
          updatedData[property] = data[property]
        }
      }
      // Ensure boolean fields are included even if false
      updatedData.default_accepts_assets = data.default_accepts_assets || false
      // Always include id for update
      updatedData.id = data.id
      
      LNbits.api
        .request(
          'PUT',
          '/bitcoinswitch/api/v1/bitcoinswitch/' + updatedData.id,
          wallet,
          updatedData
        )
        .then(response => {
          this.bitcoinswitches = _.reject(this.bitcoinswitches, function (obj) {
            return obj.id === updatedData.id
          })
          this.bitcoinswitches.push(response.data)
          this.formDialog.show = false
          this.clearFormDialog()
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    getBitcoinswitches() {
      LNbits.api
        .request(
          'GET',
          '/bitcoinswitch/api/v1/bitcoinswitch',
          this.g.user.wallets[0].adminkey
        )
        .then(response => {
          if (response.data.length > 0) {
            this.bitcoinswitches = response.data
          }
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    deleteBitcoinswitch(bitcoinswitchId) {
      LNbits.utils
        .confirmDialog('Are you sure you want to delete this pay link?')
        .onOk(() => {
          LNbits.api
            .request(
              'DELETE',
              '/bitcoinswitch/api/v1/bitcoinswitch/' + bitcoinswitchId,
              this.g.user.wallets[0].adminkey
            )
            .then(() => {
              this.bitcoinswitches = _.reject(
                this.bitcoinswitches,
                function (obj) {
                  return obj.id === bitcoinswitchId
                }
              )
            })
            .catch(function (error) {
              LNbits.utils.notifyApiError(error)
            })
        })
    },
    openCreateDialog() {
      this.clearFormDialog()
      // Add a default switch if none exist
      if (this.formDialog.data.switches.length === 0) {
        this.addSwitch()
      }
      this.formDialog.show = true
    },
    openUpdateBitcoinswitch(bitcoinswitchId) {
      const bitcoinswitch = _.findWhere(this.bitcoinswitches, {
        id: bitcoinswitchId
      })
      this.formDialog.data = _.clone(bitcoinswitch)
      // Ensure default_accepts_assets is set even if missing in the data
      if (this.formDialog.data.default_accepts_assets === undefined) {
        this.formDialog.data.default_accepts_assets = false
      }
      this.formDialog.show = true
    },
    openBitcoinswitchSettings(bitcoinswitchId) {
      const bitcoinswitch = _.findWhere(this.bitcoinswitches, {
        id: bitcoinswitchId
      })
      this.wslocation =
        'wss://' + window.location.host + '/api/v1/ws/' + bitcoinswitchId
      this.settingsDialog.data = _.clone(bitcoinswitch)
      this.settingsDialog.show = true
    },
    websocketConnector(websocketUrl) {
      if ('WebSocket' in window) {
        const ws = new WebSocket(websocketUrl)
        this.updateWsMessage('Websocket connected')
        ws.onmessage = evt => {
          this.updateWsMessage('Message received: ' + evt.data)
        }
        ws.onclose = () => {
          this.updateWsMessage('Connection closed')
        }
      } else {
        this.updateWsMessage('WebSocket NOT supported by your Browser!')
      }
    },
    updateWsMessage(message) {
      this.websocketMessage = message
    },
    async checkTaprootAvailability() {
      console.log('[BitcoinSwitch] Checking Taproot Assets availability...')
      try {
        console.log('[BitcoinSwitch] Making API request to /api/v1/extension')
        console.log('[BitcoinSwitch] User ID:', this.g.user.id)
        
        const {data} = await LNbits.api.request(
          'GET',
          '/api/v1/extension?usr=' + this.g.user.id,
          this.g.user.wallets[0].adminkey
        )
        
        console.log('[BitcoinSwitch] Extensions response:', data)
        
        const taprootExt = data.find(ext => ext.code === 'taproot_assets')
        console.log('[BitcoinSwitch] Taproot extension found:', taprootExt)
        
        this.taprootAssetsAvailable = data.some(
          ext => ext.code === 'taproot_assets' && (ext.active || ext.is_valid)
        )
        
        console.log('[BitcoinSwitch] Taproot Assets available:', this.taprootAssetsAvailable)
        
        if (this.taprootAssetsAvailable) {
          console.log('[BitcoinSwitch] Loading available assets...')
          await this.loadAvailableAssets()
        } else {
          console.log('[BitcoinSwitch] Taproot Assets not available or not active')
        }
      } catch (error) {
        console.error('[BitcoinSwitch] Failed to check taproot availability:', error)
        console.error('[BitcoinSwitch] Error details:', error.response || error)
        this.taprootAssetsAvailable = false
      }
    },
    async loadAvailableAssets() {
      if (!this.taprootAssetsAvailable) {
        console.log('[BitcoinSwitch] Skipping asset load - Taproot not available')
        return
      }
      
      console.log('[BitcoinSwitch] Loading Taproot Assets...')
      this.loadingAssets = true
      try {
        console.log('[BitcoinSwitch] Making API request to /taproot_assets/api/v1/taproot/listassets')
        const {data} = await LNbits.api.request(
          'GET',
          '/taproot_assets/api/v1/taproot/listassets',
          this.g.user.wallets[0].adminkey
        )
        console.log('[BitcoinSwitch] Assets response:', data)
        
        this.availableAssets = data.map(asset => ({
          asset_id: asset.asset_id,
          name: asset.name || `Asset ${asset.asset_id.substring(0, 8)}...`,
          balance: asset.balance || 0
        }))
        
        console.log('[BitcoinSwitch] Processed assets:', this.availableAssets)
        console.log('[BitcoinSwitch] Total assets available:', this.availableAssets.length)
      } catch (error) {
        console.error('[BitcoinSwitch] Failed to load assets:', error)
        console.error('[BitcoinSwitch] Asset load error details:', error.response || error)
        this.availableAssets = []
      } finally {
        this.loadingAssets = false
        console.log('[BitcoinSwitch] Asset loading complete')
      }
    },
    hasTaprootAssets(bitcoinswitch) {
      return bitcoinswitch.switches && 
             bitcoinswitch.switches.some(s => s.accepts_assets && s.accepted_asset_ids && s.accepted_asset_ids.length > 0)
    },
    getAssetName(assetId) {
      const asset = this.availableAssets.find(a => a.asset_id === assetId)
      return asset ? asset.name : `${assetId.substring(0, 8)}...`
    },
    clearFormDialog() {
      this.formDialog.data = {
        switches: [],
        lnurl_toggle: false,
        show_message: false,
        show_ack: false,
        show_price: 'None',
        device: 'pos',
        profit: 1,
        amount: 1,
        title: '',
        wallet: '',
        currency: 'sat',
        password: '',
        default_accepts_assets: false
      }
    },
    exportCSV() {
      LNbits.utils.exportCSV(
        this.bitcoinswitchTable.columns,
        this.bitcoinswitches
      )
    }
  },
  created() {
    console.log('[BitcoinSwitch] Extension initializing...')
    console.log('[BitcoinSwitch] User data:', this.g.user)
    
    this.getBitcoinswitches()
    this.checkTaprootAvailability()
    this.location = [window.location.protocol, '//', window.location.host].join(
      ''
    )
    this.wslocation = ['wss://', window.location.host].join('')
    
    console.log('[BitcoinSwitch] Loading currencies...')
    LNbits.api
      .request('GET', '/api/v1/currencies')
      .then(response => {
        console.log('[BitcoinSwitch] Currencies loaded:', response.data.length)
        this.currency = ['sat', 'USD', ...response.data]
      })
      .catch(error => {
        console.error('[BitcoinSwitch] Failed to load currencies:', error)
        LNbits.utils.notifyApiError(error)
      })
  }
})
