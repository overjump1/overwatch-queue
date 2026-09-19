package com.tomerady.overwatchqueue.shared

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import java.io.IOException
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

        data object Failed : Result
    }

    suspend fun fetchStatus(pairId: String): Result =
        send("GET", "/v1/pair/$pairId/state", null) { text ->
            val status = QueueStatus.json.parseToJsonElement(text).jsonObject["status"]
            status?.let { QueueStatus.decode(it.toString()) }
        }

    /** `body` has the device `kind` ("android") and its `fcm` token. */
    suspend fun register(pairId: String, body: JsonObject): Result =
        send("POST", "/v1/pair/$pairId/device", body.toString()) { null }

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
                    else -> Result.Failed
                }
            } finally {
                connection.disconnect()
            }
        } catch (e: IOException) {
            Result.Failed
        }
    }
}
