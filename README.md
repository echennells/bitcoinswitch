# Bitcoin Switch - <small>[LNbits](https://github.com/lnbits/lnbits) extension</small>

Turn things on with bitcoin - now with Taproot Assets support!

This is an enhanced version of the Bitcoin Switch extension that adds support for Taproot Assets, allowing switches to be activated with either Bitcoin or Taproot Assets.

## Features

### Original Bitcoin Switch Features
- Control GPIO pins with Lightning Network payments
- Variable time control based on payment amount
- Support for multiple switches per device
- Custom payment amounts and durations
- Optional comment support for payments
- WebSocket integration for real-time updates
- Public switch pages
- Disposable and permanent switches
- Password protection

### New Taproot Assets Support
- Accept Taproot Assets as payment
- Configure accepted asset types per switch
- Real-time asset price discovery through RFQ
- Asset amount configuration
- Automatic sat equivalent calculation
- Compatible with standard Lightning wallets for regular payments

## Requirements

- LNbits installation
- For Taproot Assets: The Taproot Assets extension must be installed and active
- For hardware control: Compatible GPIO device (e.g., Raspberry Pi)

## Usage

1. Regular Lightning Payments:
   - Configure switch with amount in sats or fiat
   - Share LNURL or QR code with users
   - Payment triggers the configured GPIO pin

2. Taproot Asset Payments:
   - Enable Taproot Assets in switch configuration
   - Select which assets to accept
   - Configure asset amounts
   - Users can pay with either sats or accepted assets

## Configuration

### Standard Settings
- Title: Name for your switch
- Amount: Payment amount (in sats or fiat)
- Duration: How long to activate the switch
- GPIO Pin: Which pin to control
- Variable Time: Optional multiplier for amount/duration
- Disabled: Temporarily disable switch
- Disposable: Single-use vs reusable switches
- Password: Optional password protection

### Taproot Asset Settings
- Accept Assets: Enable/disable Taproot Asset support
- Asset Selection: Choose which assets to accept
- Asset Amount: How many asset units to request
- Rate Discovery: Automatic through RFQ system

## Links
- [Bitcoin Switch Demo](https://bitcoinswitch.lnbits.com)
- [Video Tutorial](https://www.youtube.com/@makerbits7700)
- [Support Group](https://t.me/makerbits)

## Support
For support, please [open an issue on GitHub](https://github.com/echennells/bitcoinswitch_extension/issues).

## Credits
- Original Authors: Ben Arc, DNI
- Taproot Assets Integration: Eric Chennells
- Based on [Original Bitcoin Switch](https://github.com/lnbits/bitcoinSwitch)

## License
MIT
