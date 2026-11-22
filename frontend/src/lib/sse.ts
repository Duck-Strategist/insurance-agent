/**
 * Server-Sent Events 解析。对话接口是 POST 且要带 JWT，不能用浏览器的 EventSource，
 * 所以用 fetch 拿到 ReadableStream 自己解析（spec §7）。
 *
 * 只实现后端用到的子集：每个事件由 `event:` 和一行 `data:`（JSON）组成，事件之间空一行。
 * 网络分块可能在任意位置切断（包括一个汉字的 UTF-8 字节中间），所以按字节流解码、按空行切事件。
 */

export interface SseEvent {
  event: string
  data: string
}

function parseBlock(block: string): SseEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
  }
  return data.length > 0 ? { event, data: data.join('\n') } : null
}

export async function* readSse(stream: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = stream.getReader()
  // stream: true 让解码器保留被切断的多字节字符，等下一块再拼
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      let sep = buffer.indexOf('\n\n')
      while (sep !== -1) {
        const parsed = parseBlock(buffer.slice(0, sep))
        buffer = buffer.slice(sep + 2)
        if (parsed) yield parsed
        sep = buffer.indexOf('\n\n')
      }
    }
    buffer += decoder.decode()
    const last = parseBlock(buffer.trim())
    if (last) yield last
  } finally {
    reader.releaseLock()
  }
}
