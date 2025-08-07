// Utility functions
const utils = {
  async apiRequest(method, url, wallet, data = null) {
    try {
      const response = await LNbits.api.request(method, url, wallet, data);
      return response.data;
    } catch (error) {
      LNbits.utils.notifyApiError(error);
      throw error;
    }
  },

  prepareFormData(data) {
    // Filter out undefined/null/empty values
    const updatedData = Object.entries(data)
      .filter(([_, value]) => value !== undefined && value !== null && value !== '')
      .reduce((acc, [key, value]) => ({ ...acc, [key]: value }), {});
    
    // Apply global taproot settings if needed
    if (data.accepts_assets && data.switches) {
      updatedData.switches = data.switches.map(sw => ({
        ...sw,
        accepts_assets: true,
        accepted_asset_ids: data.accepted_asset_ids || []
      }));
    }
    
    return updatedData;
  },

  initWebSocket(url, callbacks = {}) {
    if (!('WebSocket' in window)) {
      callbacks.onError?.('WebSocket NOT supported by your Browser!');
      return null;
    }

    const ws = new WebSocket(url);
    
    ws.addEventListener('open', () => callbacks.onOpen?.('Websocket connected'));
    ws.addEventListener('message', (evt) => callbacks.onMessage?.(evt.data));
    ws.addEventListener('close', () => callbacks.onClose?.('Connection closed'));
    ws.addEventListener('error', (error) => callbacks.onError?.(error.message));
    
    return ws;
  }
};

// Main Vue application
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
      
      // Table configuration
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
      
      // Dialog states
      settingsDialog: {
        show: false,
        data: {}
      },
      formDialog: {
        show: false,
        data: this.getDefaultFormData()
      },
      qrCodeDialog: {
        show: false,
        data: null
      }
    };
  },

  computed: {
    wsMessage() {
      return this.websocketMessage;
    },
    
    currentSwitch() {
      if (!this.qrCodeDialog.data || !this.lnurlValue) return null;
      return this.qrCodeDialog.data.switches.find(s => s.lnurl === this.lnurlValue);
    },
    
    adminKey() {
      return this.g.user.wallets[0].adminkey;
    }
  },

  methods: {
    // Form handling
    getDefaultFormData() {
      return {
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
        accepts_assets: false,
        accepted_asset_ids: []
      };
    },

    clearFormDialog() {
      this.formDialog.data = this.getDefaultFormData();
    },

    // Switch management
    addSwitch() {
      this.formDialog.data.switches.push({
        amount: 10,
        pin: 0,
        duration: 1000,
        variable: false,
        comment: false
      });
    },

    removeSwitch() {
      this.formDialog.data.switches.pop();
    },

    handleAcceptAssetsChange(val) {
      if (!val) {
        this.formDialog.data.accepted_asset_ids = [];
      }
      // Update all switches to use global setting
      this.formDialog.data.switches.forEach(sw => {
        sw.accepts_assets = val;
        sw.accepted_asset_ids = val ? this.formDialog.data.accepted_asset_ids : [];
      });
    },

    // API interactions
    async createBitcoinswitch(wallet, data) {
      try {
        const formData = utils.prepareFormData(data);
        const response = await utils.apiRequest(
          'POST',
          '/bitcoinswitch/api/v1/bitcoinswitch',
          wallet,
          formData
        );
        
        this.bitcoinswitches.push(response);
        this.formDialog.show = false;
        this.clearFormDialog();
      } catch (error) {
        // Error handled by utils.apiRequest
      }
    },

    async updateBitcoinswitch(wallet, data) {
      try {
        const formData = utils.prepareFormData(data);
        formData.id = data.id;
        
        const response = await utils.apiRequest(
          'PUT',
          `/bitcoinswitch/api/v1/bitcoinswitch/${formData.id}`,
          wallet,
          formData
        );
        
        this.bitcoinswitches = this.bitcoinswitches.filter(obj => obj.id !== formData.id);
        this.bitcoinswitches.push(response);
        this.formDialog.show = false;
        this.clearFormDialog();
      } catch (error) {
        // Error handled by utils.apiRequest
      }
    },

    async getBitcoinswitches() {
      try {
        const data = await utils.apiRequest(
          'GET',
          '/bitcoinswitch/api/v1/bitcoinswitch',
          this.adminKey
        );
        if (data.length > 0) {
          this.bitcoinswitches = data;
        }
      } catch (error) {
        // Error handled by utils.apiRequest
      }
    },

    async deleteBitcoinswitch(bitcoinswitchId) {
      try {
        await LNbits.utils.confirmDialog('Are you sure you want to delete this pay link?');
        
        await utils.apiRequest(
          'DELETE',
          `/bitcoinswitch/api/v1/bitcoinswitch/${bitcoinswitchId}`,
          this.adminKey
        );
        
        this.bitcoinswitches = this.bitcoinswitches.filter(obj => obj.id !== bitcoinswitchId);
      } catch (error) {
        // User cancelled or error handled by utils.apiRequest
      }
    },

    // Dialog management
    openQrCodeDialog(bitcoinswitchId) {
      const bitcoinswitch = this.bitcoinswitches.find(sw => sw.id === bitcoinswitchId);
      this.qrCodeDialog.data = {...bitcoinswitch};
      this.qrCodeDialog.data.url = `${window.location.protocol}//${window.location.host}`;
      this.lnurlValue = this.qrCodeDialog.data.switches[0].lnurl;
      
      const wsUrl = `wss://${window.location.host}/api/v1/ws/${bitcoinswitchId}`;
      utils.initWebSocket(wsUrl, {
        onOpen: msg => this.updateWsMessage(msg),
        onMessage: data => this.updateWsMessage(`Message received: ${data}`),
        onClose: msg => this.updateWsMessage(msg),
        onError: msg => this.updateWsMessage(`WebSocket error: ${msg}`)
      });
      
      this.qrCodeDialog.show = true;
    },

    openCreateDialog() {
      this.clearFormDialog();
      if (this.formDialog.data.switches.length === 0) {
        this.addSwitch();
      }
      this.formDialog.show = true;
    },

    openUpdateBitcoinswitch(bitcoinswitchId) {
      const bitcoinswitch = this.bitcoinswitches.find(sw => sw.id === bitcoinswitchId);
      this.formDialog.data = {...bitcoinswitch};
      
      // Extract global taproot settings
      if (bitcoinswitch.switches?.length > 0) {
        const firstSwitchWithAssets = bitcoinswitch.switches.find(sw => sw.accepts_assets);
        if (firstSwitchWithAssets) {
          this.formDialog.data.accepts_assets = true;
          this.formDialog.data.accepted_asset_ids = firstSwitchWithAssets.accepted_asset_ids || [];
        } else {
          this.formDialog.data.accepts_assets = false;
          this.formDialog.data.accepted_asset_ids = [];
        }
      }
      
      this.formDialog.show = true;
    },

    openBitcoinswitchSettings(bitcoinswitchId) {
      const bitcoinswitch = this.bitcoinswitches.find(sw => sw.id === bitcoinswitchId);
      this.wslocation = `wss://${window.location.host}/api/v1/ws/${bitcoinswitchId}`;
      this.settingsDialog.data = {...bitcoinswitch};
      this.settingsDialog.show = true;
    },

    // Taproot functionality
    async checkTaprootAvailability() {
      try {
        const data = await utils.apiRequest(
          'GET',
          `/api/v1/extension?usr=${this.g.user.id}`,
          this.adminKey
        );
        
        this.taprootAssetsAvailable = data.some(
          ext => ext.code === 'taproot_assets' && (ext.active || ext.is_valid)
        );
        
        if (this.taprootAssetsAvailable) {
          await this.loadAvailableAssets();
        }
      } catch (error) {
        this.taprootAssetsAvailable = false;
      }
    },

    async loadAvailableAssets() {
      if (!this.taprootAssetsAvailable) return;
      
      this.loadingAssets = true;
      try {
        const data = await utils.apiRequest(
          'GET',
          '/taproot_assets/api/v1/taproot/listassets',
          this.adminKey
        );
        
        this.availableAssets = data.map(asset => ({
          asset_id: asset.asset_id,
          name: asset.name || `Asset ${asset.asset_id.substring(0, 8)}...`,
          balance: asset.balance || 0
        }));
      } catch (error) {
        this.availableAssets = [];
      } finally {
        this.loadingAssets = false;
      }
    },

    hasTaprootAssets(bitcoinswitch) {
      return bitcoinswitch.switches?.some(s => 
        s.accepts_assets && s.accepted_asset_ids?.length > 0
      );
    },

    getAssetName(assetId) {
      const asset = this.availableAssets.find(a => a.asset_id === assetId);
      return asset ? asset.name : `${assetId.substring(0, 8)}...`;
    },

    // Form submission
    sendFormData() {
      const method = this.formDialog.data.id ? 'updateBitcoinswitch' : 'createBitcoinswitch';
      this[method](this.adminKey, this.formDialog.data);
    },

    // Utility methods
    updateWsMessage(message) {
      this.websocketMessage = message;
    },

    exportCSV() {
      LNbits.utils.exportCSV(this.bitcoinswitchTable.columns, this.bitcoinswitches);
    }
  },

  created() {
    this.getBitcoinswitches();
    this.checkTaprootAvailability();
    
    this.location = `${window.location.protocol}//${window.location.host}`;
    this.wslocation = `wss://${window.location.host}`;
    
    // Load available currencies
    utils.apiRequest('GET', '/api/v1/currencies')
      .then(data => {
        this.currency = ['sat', 'USD', ...data];
      })
      .catch(() => {
        // Error already handled by utils.apiRequest
      });
  }
});