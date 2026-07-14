import { MeetingChatService } from './chat';

let passed = 0;
let failed = 0;

function expect(name: string, actual: unknown, expected: unknown) {
  if (JSON.stringify(actual) === JSON.stringify(expected)) {
    console.log(`PASS ${name}`);
    passed++;
  } else {
    console.log(`FAIL ${name}`);
    console.log(`  expected: ${JSON.stringify(expected)}`);
    console.log(`  actual:   ${JSON.stringify(actual)}`);
    failed++;
  }
}

class FakeElement {
  isConnected = true;
  tagName: string;
  value = '';
  textContent = '';
  clickCount = 0;
  onInput: (() => void) | null = null;

  constructor(tagName: string) {
    this.tagName = tagName;
  }

  focus() {}

  click() {
    this.clickCount++;
  }

  hasAttribute(name: string): boolean {
    return name === 'disabled' ? false : false;
  }

  dispatchEvent(event: Event): boolean {
    if (event.type === 'input') this.onInput?.();
    return true;
  }
}

class FakeDocument {
  readonly chatButton = new FakeElement('BUTTON');
  readonly sendButton = new FakeElement('BUTTON');
  readonly firstInput = new FakeElement('TEXTAREA');
  readonly replacementInput = new FakeElement('TEXTAREA');
  currentInput = this.firstInput;

  constructor() {
    this.firstInput.onInput = () => {
      this.firstInput.isConnected = false;
      this.currentInput = this.replacementInput;
    };
  }

  querySelector(selector: string): FakeElement | null {
    if (selector.includes('Send a message') || selector.includes('textarea[aria-label*="chat"]')) {
      return this.currentInput;
    }
    if (selector.startsWith('button[aria-label') || selector.startsWith('button[data-tooltip')) {
      if (selector.includes('Send') || selector.includes('send')) return this.sendButton;
      return this.chatButton;
    }
    return null;
  }
}

class FakePage {
  readonly browserState: Record<string, unknown> = {
    __vexaPeoplePanelSnapshotInFlight: true,
    __vexaChatSendInFlight: false,
  };
  readonly document = new FakeDocument();

  isClosed(): boolean {
    return false;
  }

  async evaluate<T, A>(fn: (arg: A) => T | Promise<T>, arg?: A): Promise<T> {
    const globalRecord = globalThis as any;
    const priorWindow = globalRecord.window;
    const priorDocument = globalRecord.document;
    globalRecord.window = this.browserState;
    globalRecord.document = this.document;
    try {
      return await fn(arg as A);
    } finally {
      globalRecord.window = priorWindow;
      globalRecord.document = priorDocument;
    }
  }
}

async function main() {
  const page = new FakePage();
  const service = new MeetingChatService(page as any, 'google_meet', 1, 'Kabosu');
  setTimeout(() => {
    page.browserState.__vexaPeoplePanelSnapshotInFlight = false;
  }, 30);

  const startedAt = Date.now();
  const result = await service.sendMessage('ロック付きメッセージ');

  expect('chat waits for an in-flight People snapshot', Date.now() - startedAt >= 25, true);
  expect('chat send succeeds after the panel lock is acquired', result, true);
  expect('detached input is reacquired and refilled', page.document.replacementInput.value, 'ロック付きメッセージ');
  expect('send button is clicked exactly once', page.document.sendButton.clickCount, 1);
  expect('chat panel lock is released after send', page.browserState.__vexaChatSendInFlight, false);

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
