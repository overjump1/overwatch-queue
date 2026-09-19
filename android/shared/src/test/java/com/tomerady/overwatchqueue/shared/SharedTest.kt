package com.tomerady.overwatchqueue.shared

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingTest {
    private val id = "0123456789abcdef0123456789abcdef"

    @Test fun parsesTheQrCode() = assertEquals(id, Pairing.parse("owq://pair?id=$id"))

    @Test fun lowercasesAndTrims() = assertEquals(id, Pairing.parse("  owq://pair?id=${id.uppercase()}\n"))

    @Test fun findsIdAmongOtherParameters() = assertEquals(id, Pairing.parse("owq://pair?v=1&id=$id"))

    @Test fun rejectsOtherSchemesAndHosts() {
        assertNull(Pairing.parse("https://pair?id=$id"))
        assertNull(Pairing.parse("owq://other?id=$id"))
    }

    @Test fun rejectsBadIds() {
        assertNull(Pairing.parse("owq://pair?id=${id.dropLast(1)}"))
        assertNull(Pairing.parse("owq://pair?id=${id.dropLast(1)}g"))
        assertNull(Pairing.parse("owq://pair"))
        assertNull(Pairing.parse("not a url at all"))
    }
}

class QueueStatusTest {
    @Test fun decodesTheWorkerStatus() {
        val status = QueueStatus.decode("""{"state":"found","mode":"quickPlay","startedAt":1000,"foundAt":1125}""")
        assertEquals(QueueStatus(QueueState.FOUND, GameMode.QUICK_PLAY, 1000.0, 1125.0), status)
        assertEquals(125.0, status!!.waited!!, 0.0)
        assertEquals("Match found!", status.title)
    }

    @Test fun readsUnknownValuesAsIdleWithNoMode() {
        assertEquals(QueueStatus.Idle, QueueStatus.decode("""{"state":"somethingNew","mode":"newMode","extra":1}"""))
        assertEquals(QueueStatus.Idle, QueueStatus.decode("{}"))
        assertEquals(QueueStatus.Idle, QueueStatus.decode("""{"state":"idle","mode":null,"startedAt":null,"foundAt":null}"""))
        assertNull(QueueStatus.decode("not json"))
    }

    @Test fun onlyAFreshMatchIsFresh() {
        val match = QueueStatus(QueueState.FOUND, foundAt = 1000.0)
        assertTrue(match.isFreshMatch(now = 1059.0))
        assertFalse(match.isFreshMatch(now = 1060.0))
        assertFalse(match.copy(state = QueueState.PLAYING).isFreshMatch(now = 1001.0))
    }

    @Test fun formatsClocks() {
        assertEquals("0:00", clockString(-5.0))
        assertEquals("2:05", clockString(125.9))
        assertEquals("1:00:01", clockString(3601.0))
    }
}

class QueuePushTest {
    @Test fun parsesAWorkerPush() {
        val push = QueuePush.parse(
            mapOf("event" to "start", "status" to """{"state":"queueing","mode":"competitive","startedAt":995,"foundAt":null}""",
                "sentAt" to "1000", "alert" to "queue"),
        )
        assertEquals(QueuePush.Event.START, push!!.event)
        assertEquals(QueuePush.Alert.QUEUE, push.alert)
        assertEquals(QueueState.QUEUEING, push.status.state)
        assertEquals(1000L, push.sentAt)
    }

    @Test fun readsTheLingerOnEnd() {
        val push = QueuePush.parse(mapOf("event" to "end", "status" to """{"state":"idle"}""", "linger" to "600"))
        assertEquals(600L, push!!.linger)
        assertNull(push.alert)
    }

    @Test fun ignoresOtherMessages() {
        assertNull(QueuePush.parse(emptyMap()))
        assertNull(QueuePush.parse(mapOf("event" to "somethingElse", "status" to "{}")))
    }
}
