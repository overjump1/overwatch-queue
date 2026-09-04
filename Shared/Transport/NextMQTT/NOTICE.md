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

Both additions were needed because `MQTTTransport.swift` relies on retained state and a
Last Will for its presence/reconnect semantics — see the comments there.
