package com.tomerady.overqueue.shared

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingTest {
    private val id = "0123456789abcdef0123456789abcdef"

    @Test fun parsesTheQrCode() = assertEquals(id, Pairing.parse("owq://pair?id=$id"))

    @Test fun theRealAppIgnoresTheDevAppsCode() = assertNull(Pairing.parse("overqueue-dev://pair?id=$id"))

    @Test fun theDevAppOnlyTakesItsOwnCode() {
        val real = Pairing.schemes
        Pairing.schemes = setOf("overqueue-dev")
        try {
            assertEquals(id, Pairing.parse("overqueue-dev://pair?id=$id"))
            assertNull(Pairing.parse("overqueue://pair?id=$id"))
            assertNull(Pairing.parse("owq://pair?id=$id"))
        } finally {
            Pairing.schemes = real
        }
    }

    @Test fun lowercasesAndTrims() =assertEquals(id, Pairing.parse("  owq://pair?id=${id.uppercase()}\n"))

    @Test fun findsIdAmongOtherParameters() = assertEquals(id, Pairing.parse("owq://pair?v=1&id=$id"))

    @Test fun rejectsOtherSchemesAndHosts() {
        assertNull(Pairing.parse("https://pair?id=$id"))
        assertNull(Pairing.parse("owq://other?id=$id"))
    }

    @Test fun saysNothingAgainstACodeThatPairs() = assertNull(Pairing.rejection("owq://pair?id=$id", "OverQueue"))

    @Test fun saysWhichAppACodeIsFrom() {
        val said = Pairing.rejection("overqueue-dev://pair?id=$id", "OverQueue")
        assertTrue(said!!.contains("OverQueue Dev"))
    }

    @Test fun namesTheLinksWhenTheAppIsBuiltWithTheWrongOnes() {
        val real = Pairing.schemes
        Pairing.schemes = setOf("overqueue-dev")
        try {
            val said = Pairing.rejection("overqueue://pair?id=$id", "OverQueue")!!
            assertTrue(said.contains("overqueue://"))
            assertTrue(said.contains("overqueue-dev://"))
        } finally {
            Pairing.schemes = real
        }
    }

    @Test fun saysADamagedCodeIsDamaged() =
        assertTrue(Pairing.rejection("owq://pair?id=nope", "OverQueue")!!.contains("damaged"))

    @Test fun saysAnythingElseIsntAPairingCode() =
        assertTrue(Pairing.rejection("https://example.com", "OverQueue")!!.contains("isn't a pairing code"))

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

class UpdatesTest {
    private fun release(vararg names: String) = names.joinToString(
        prefix = """{"tag_name":"v2.0.42","assets":[""",
        postfix = "]}",
    ) { """{"name":"$it","browser_download_url":"https://example.test/$it","size":1234}""" }

    @Test fun padsVersionsToThreeParts() {
        assertArrayEquals(intArrayOf(2, 0, 0), Updates.parse("2.0"))
        assertArrayEquals(intArrayOf(2, 0, 42), Updates.parse("v2.0.42"))
    }

    @Test fun stopsAtTheFirstNonNumber() {
        assertArrayEquals(intArrayOf(0, 0, 0), Updates.parse("0.0.0-dev"))
        assertArrayEquals(intArrayOf(2, 1, 0), Updates.parse("2.1-rc1"))
    }

    @Test fun rejectsWhatIsntAVersion() {
        assertNull(Updates.parse(null))
        assertNull(Updates.parse("dev"))
    }

    @Test fun comparesNumericallyNotAsText() {
        assertTrue(Updates.isNewer("2.0.42", "2.0.9"))
        assertFalse(Updates.isNewer("2.0.9", "2.0.42"))
        assertFalse(Updates.isNewer("2.0", "2.0.0"))
        assertFalse(Updates.isNewer("dev", "2.0.0"))
    }

    @Test fun buildsFromSourceDontCheck() {
        assertFalse(Updates.checksForUpdates("0.0.0-dev"))
        assertFalse(Updates.checksForUpdates(null))
        assertTrue(Updates.checksForUpdates("2.0.42"))
    }

    @Test fun offersANewerApk() {
        val found = Updates.findUpdate(release("OverQueue-Setup-2.0.42.exe", "OverQueue-2.0.42.apk"), "2.0.40")
        assertEquals(Updates.Release("2.0.42", "https://example.test/OverQueue-2.0.42.apk", 1234), found)
    }

    @Test fun readsTheVersionFromTheAssetNotTheTag() {
        // release.yml copies unchanged apps forward, so a newer tag can hold this build's APK.
        assertNull(Updates.findUpdate(release("OverQueue-2.0.40.apk"), "2.0.40"))
    }

    @Test fun ignoresTheOtherPlatforms() {
        assertNull(Updates.findUpdate(release("OverQueue-Setup-2.0.42.exe", "OverQueue-2.0.42.ipa"), "2.0.40"))
    }

    @Test fun survivesAReleaseWithNothingInIt() {
        assertNull(Updates.findUpdate(release(), "2.0.40"))
        assertNull(Updates.findUpdate("not json at all", "2.0.40"))
    }
}
