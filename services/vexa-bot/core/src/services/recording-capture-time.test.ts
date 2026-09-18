import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import http from 'node:http';
import { RecordingService } from './recording';

// Isolate capture from the executable bot entrypoint. Recording must keep its
// timestamp even when live transcription/SegmentPublisher is disabled.
const indexPath = require.resolve('../index');
require.cache[indexPath] = { exports: { getSegmentPublisher: () => null } } as NodeModule;
const { UnifiedRecordingPipeline } = require('./audio-pipeline');

async function testCaptureTimestampSurvivesDelayedUploads() {
  const received: any[] = [];
  const server = http.createServer((req, res) => {
    const parts: Buffer[] = [];
    req.on('data', (chunk) => parts.push(chunk));
    req.on('end', () => {
      const body = Buffer.concat(parts).toString();
      const metadata = body.match(/name="metadata"\r\nContent-Type: application\/json\r\n\r\n([^\r]+)/);
      assert.ok(metadata, 'multipart upload contains metadata');
      received.push(JSON.parse(metadata[1]));
      res.writeHead(201);
      res.end('{}');
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address() as { port: number };
  const originalNow = Date.now;
  const start = Date.parse('2026-09-18T01:00:00Z');
  let now = start;
  Date.now = () => now;
  const source = new EventEmitter() as EventEmitter & { start: () => Promise<void>; stop: () => Promise<void> };
  source.start = async () => { source.emit('started'); };
  source.stop = async () => {
    source.emit('chunk', { data: Buffer.from('final'), format: 'webm', seq: 1, isFinal: true });
  };
  const recordingService = new RecordingService(42, 'timestamp-test');
  const pipeline = new UnifiedRecordingPipeline({
    source, recordingService,
    uploadUrl: `http://127.0.0.1:${address.port}/internal/recordings/upload`,
    token: 'test', platform: 'gmeet',
  });
  try {
    await pipeline.start();
    now += 30000;
    source.emit('started'); // duplicate signal must not move the time origin
    source.emit('chunk', { data: Buffer.from('first'), format: 'webm', seq: 0, isFinal: false });
    await pipeline.stop();
    assert.equal(received.length, 2);
    assert.deepEqual(received.map((m) => m.chunk_seq), [0, 1]);
    assert.deepEqual(received.map((m) => m.is_final), [false, true]);
    assert.ok(received.every((m) => m.start_time_utc === '2026-09-18T01:00:00.000Z'));
    assert.equal(recordingService.getStartTime(), start);
    console.log('PASS capture timestamps survive delayed chunk uploads without live transcription');
  } finally {
    Date.now = originalNow;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}

testCaptureTimestampSurvivesDelayedUploads().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
