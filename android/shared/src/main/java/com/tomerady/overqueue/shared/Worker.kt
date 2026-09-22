package com.tomerady.overqueue.shared

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import java.net.HttpURLConnection
import java.net.URL

/** The Cloudflare worker: the only thing the apps talk to. */
object Worker {
    const val DEFAULT_URL = "https://overwatch-queue-push-relay.tomerady.workers.dev"

    /** Debug builds can point this at a local worker (see the README). */
    @Volatile
    var baseUrl: String = DEFAULT_URL

    sealed interface Result {
        data class Ok(val status: QueueStatus?) : Result

        /** The PC made a new pairing code; this one no longer works. */
        data object Reset : Result

        /** [reason] says why, for the log: an HTTP status or the network error. */
        data class Failed(val reason: String) : Result
    }

    suspend fun fetchStatus(pairId: String): Result =
        send("GET", "/v1/pair/$pairId/state", null) { text ->
            val status = QueueStatus.json.parseToJsonElement(text).jsonObject["status"]
            status?.let { QueueStatus.decode(it.toString()) }
        }

    /** `body` has the device `kind` ("android") and its `fcm` token. */
    suspend fun register(pairId: String, body: JsonObject): Result =
        send("POST", "/v1/pair/$pairId/device", body.toString()) { null }

    /** Unpairing: the worker drops this phone's token. */
    suspend fun forget(pairId: String): Result =
        send("DELETE", "/v1/pair/$pairId/device?kind=android", null) { null }

    private suspend fun send(
        method: String,
        path: String,
        body: String?,
        decode: (String) -> QueueStatus?,
    ): Result = withContext(Dispatchers.IO) {
        try {
            val connection = URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection
            try {
                connection.requestMethod = method
                connection.connectTimeout = 10_000
                connection.readTimeout = 10_000
                connection.setRequestProperty("Content-Type", "application/json")
                if (body != null) {
                    connection.doOutput = true
                    connection.outputStream.use { it.write(body.toByteArray()) }
                }
                when (connection.responseCode) {
                    410 -> Result.Reset
                    200 -> {
                        val text = connection.inputStream.bufferedReader().use { it.readText() }
                        Result.Ok(runCatching { decode(text) }.getOrNull())
                    }
                    else -> {
                        val text = runCatching { connection.errorStream?.bufferedReader()?.use { it.readText() } }.getOrNull()
                        Result.Failed("HTTP ${connection.responseCode} ${text.orEmpty()}".trim())
                    }
                }
            } finally {
                connection.disconnect()
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // IOException for the network; anything else (a malformed URL, a bad reply) is a failure too.
            Result.Failed(e.toString())
        }
    }
}
