import { readSse } from './sse'

function streamOf(chunks: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(c)
      controller.close()
    },
  })
}

async function collect(chunks: Uint8Array[]) {
  const out = []
  for await (const ev of readSse(streamOf(chunks))) out.push(ev)
  return out
}

test('跨块切断的事件和被切开的汉字都能正确还原', async () => {
  const bytes = new TextEncoder().encode(
    'event: token\ndata: {"text":"等待期"}\n\nevent: done\ndata: {}\n\n',
  )
  // 在"等"字的 UTF-8 字节中间切开，并把事件分隔符也切开
  const cut1 = 22
  const cut2 = bytes.length - 3
  const events = await collect([bytes.slice(0, cut1), bytes.slice(cut1, cut2), bytes.slice(cut2)])
  expect(events).toEqual([
    { event: 'token', data: '{"text":"等待期"}' },
    { event: 'done', data: '{}' },
  ])
})

test('兼容 CRLF，忽略没有 data 的块，最后一个事件没有空行结尾也能读到', async () => {
  const text = 'event: status\r\ndata: {"text":"检索中"}\r\n\r\n: 注释行\n\nevent: done\ndata: {}'
  expect(await collect([new TextEncoder().encode(text)])).toEqual([
    { event: 'status', data: '{"text":"检索中"}' },
    { event: 'done', data: '{}' },
  ])
})
