# NextMQTT (vendored)

Source: https://github.com/followben/NextMQTT (MIT License, Copyright (c) Ben Stovold 2019)

Vendored here — rather than pulled in as a Swift Package — because it's the only
MQTT client found that both runs on watchOS (it's pure Foundation/`URLSessionStreamTask`,
unlike CocoaMQTT which drags in a CFNetwork-dependent socket library that doesn't build
for watchOS) and is small enough to patch directly.

Local changes from upstream:
- `ConnectPacket`/`ConnectFlags` (Packet.swift) and `MQTT.init` (MQTT.swift): added Last
  Will and Testament support (topic/message/QoS/retain), which upstream doesn't implement.
- `PublishPacket.init` (Packet.swift) and `MQTT.publish`/`sendPublish` (MQTT.swift): added
  a `retain` parameter, which upstream doesn't expose.
- `ConnackPacket`/`SubackPacket`/`UnsubackPacket`/`PublishPacket` decoding (Packet.swift):
  generically skip any MQTT5 properties the decoder doesn't otherwise care about (via
  `skipMQTTPropertyValue`), instead of throwing on the first one beyond what upstream
  expected — a real broker (Mosquitto) routinely attaches properties upstream didn't
  handle, which turned an accepted CONNACK/SUBACK/UNSUBACK into a reported failure, and
  for PUBLISH silently corrupted the payload since the property bytes were never skipped.
- `MQTT.reconnect()` (MQTT.swift): exponential backoff capped at 30s instead of a flat
  5s retry forever, so a long outage (PC asleep or off) doesn't hammer a connect attempt
  every few seconds indefinitely.

Both additions were needed because `MQTTTransport.swift` relies on retained state and a
Last Will for its presence/reconnect semantics — see the comments there.
