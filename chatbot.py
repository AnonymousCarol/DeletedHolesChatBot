#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# LICENSE: GNU Affero General Public License, version 3
# required packages: python-telegram-bot

from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import logging, time, random, datetime, os, sys, hmac, hashlib, threading

logging.basicConfig(format='%(asctime)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

now = lambda: time.time()
bstr = lambda s: str(s).encode('ascii')
hmac_md5 = lambda key, msg: hmac.new(key, bstr(msg), digestmod=hashlib.md5).hexdigest()

bot = None
SG_ID = int(os.environ['SG_ID'])
TOKEN = os.environ['TOKEN']
SECRET = bstr(os.environ['SECRET'])

class G:
    salt = ''
    refresh = revoke = -1

names_in_use = {}
allowed_status = ("creator", "administrator", "member", "restricted")

challenges, names = ['foo bar'], ['Alice', 'Bob', 'Carol']
if all([os.path.exists(i) for i in ('names', 'challenges')]):
    challenges = open('challenges').readlines()
    names = [i.strip().title() for i in open('names').readlines()]

class STR: pass
STR.challenge_template = '请输入该帖的作者 ID (不区分大小写):\n' \
    'https://bbs.pku.edu.cn/v2/post-read-single.php?bid=22&postid=%s'
STR.welcome = '请 /verify 认证后获得邀请链接'
STR.verified = '你已经在群里，如需帮助请见群简介里的说明'
STR.too_many = '请 24 小时后再试'
STR.succeeded = '认证成功，欢迎加入\n' \
    '接下来向本 bot 发送的消息会被匿名转发到群里，使用前请阅读群简介里的说明\n\n' \
    '入群链接 (1 分钟内有效):\n%s'
STR.failed = '回答错误，请重试'
STR.forwarded = '消息已转发，如需删除请回复以下命令：\n/delete %s %s'
STR.deleted = '已删除'
STR.unsupported = '不支持的消息'
STR.whoami = '你当前的标签是 [%s] %s'
STR.tag_reset = '<b>[Bot] </b>匿名标签已重置'

class DB:
    def __init__(self):
        self.dict = dict()
        self.ttl = dict()
    def incr(self, key):
        r = int(self.get(key) or 0) + 1
        self.dict[key] = r
        return r
    def delete(self, key):
        if key in self.dict:
            del self.dict[key]
        if key in self.ttl:
            del self.ttl[key]
    def expire(self, key, ex):
        self.ttl[key] = ex+now() if ex else None
    def get(self, key):
        if key not in self.dict:
            return None
        if key in self.ttl and self.ttl[key] < now():
            self.delete(key)
            return None
        return self.dict[key]
    def set(self, key, value, ex=None):
        self.dict[key] = value
        self.expire(key, ex)

db = DB()

def generate_hash(user_id):
    tag = hmac_md5(G.salt, user_id)[:4]
    name = names_in_use.get(tag)
    if name is None:
        for i in range(len(names)):
            name = names[(i+int(tag, 16))%len(names)]
            if name not in names_in_use.values():
                names_in_use[tag] = name
                break
    return tag, name or 'Unnamed'

def generate_link():
    link = str(bot.export_chat_invite_link(SG_ID))
    db.set("invite", link, ex=60)
    print(link)
    return link

def get_invite_link():
    link = db.get("invite") or generate_link()
    G.revoke = now() + 100
    return link

def fetch_member_status(user_id):
    try:
        member = bot.get_chat_member(SG_ID, user_id)
        return member.status in allowed_status
    except: pass
    return False

def user_in_group(user_id, use_cache=True):
    k = "auth:%s"%user_id
    if use_cache and db.get(k):
        return db.get(k) > 0
    r = +1 if fetch_member_status(user_id) else -1
    db.set(k, r, ex=600)
    return r > 0

def get_challenge():
    question, answer = random.choice(challenges).split()
    return STR.challenge_template%question, answer

def auth_rate_limit(user_id, tag, n, exp=72000):
    key = "count:%s:%s"%(tag, user_id)
    count = db.incr(key)
    if count and int(count) > n:
        return True
    db.expire(key, exp)
    return False

def ping(bot, update):
    update.message.reply_text('pong')

def start(bot, update):
    user_id = update.message.from_user.id
    text = STR.verified if user_in_group(user_id) else STR.welcome
    update.message.reply_text(text)

def delete(bot, update, args):
    try:
        msg_id, key = args
        if key != hmac_md5(SECRET, msg_id): raise
        bot.delete_message(chat_id=SG_ID, message_id=int(msg_id))
        update.message.reply_text(STR.deleted)
    except: pass

def whoami(bot, update):
    user_id = update.message.from_user.id
    tag, name = generate_hash(user_id)
    return update.message.reply_text(STR.whoami%(tag, name))

def verify(bot, update):
    user_id = update.message.from_user.id
    if user_in_group(user_id, use_cache=False):
        return update.message.reply_text(STR.verified)
    if auth_rate_limit(user_id, 'auth', 10):
        return update.message.reply_text(STR.too_many)
    question, answer = get_challenge()
    db.set("answer:%s"%user_id, answer, ex=3600)
    update.message.reply_text(question)

def forward_message(bot, msg):
    if G.refresh is None:
        bot.send_message(SG_ID, STR.tag_reset, parse_mode="HTML")

    tag, name = generate_hash(msg.from_user.id)
    txt = msg.text or msg.caption or ''
    if txt.startswith('//'):
        txt = txt[2:].strip()
        tag, name = '*', 'Anonymous'
    else:
        txt = msg.text_html or msg.caption_html or ''
    txt = '<b>[%s] </b>'%name + txt

    if msg.photo:
        r = bot.send_photo(SG_ID, msg.photo[0].file_id, caption=txt, parse_mode="HTML")
    elif msg.video:
        r = bot.send_video(SG_ID, msg.video.file_id, caption=txt, parse_mode="HTML")
    elif msg.document:
        r = bot.send_document(SG_ID, msg.document.file_id, caption=txt, parse_mode="HTML")
    elif msg.voice:
        r = bot.send_voice(SG_ID, msg.voice.file_id)
    elif txt:
        r = bot.send_message(SG_ID, txt, parse_mode="HTML")
    else:
        return None

    G.refresh = now() + 10000
    return r.message_id

def message(bot, update):
    msg = update.message
    user_id = msg.from_user.id

    if user_in_group(user_id):
        msg_id = forward_message(bot, msg)
        if msg_id is None:
            return msg.reply_text(STR.unsupported)
        text = STR.forwarded%(msg_id, hmac_md5(SECRET, msg_id))
        return msg.reply_text(text, reply_to_message_id=msg.message_id)

    answer = db.get("answer:%s"%user_id)
    if answer is None:
        return start(bot, update)
    db.delete("answer:%s"%user_id)
    if msg.text.strip().lower() == answer.lower():
        db.set("auth:%s"%user_id, 1, ex=100)
        msg.reply_text(STR.succeeded%get_invite_link())
    else:
        msg.reply_text(STR.failed)

def error(bot, update, error):
    logger.warning('Update "%s" caused error "%s"', update, error)

def timer():
    while 1:
        try:
            t = now()
            if G.refresh and t > G.refresh:
                G.salt = os.urandom(16)
                G.refresh = None
                names_in_use.clear()
            if G.revoke and t > G.revoke:
                generate_link()
                G.revoke = None
        except: pass
        time.sleep(1)

updater = Updater(TOKEN)
bot = updater.bot
dp = updater.dispatcher

dp.add_handler(CommandHandler("ping", ping))
dp.add_handler(CommandHandler("help", start, Filters.private))
dp.add_handler(CommandHandler("start", start, Filters.private))
dp.add_handler(CommandHandler("verify", verify, Filters.private))
dp.add_handler(CommandHandler("whoami", whoami, Filters.private))
dp.add_handler(CommandHandler("delete", delete, Filters.private, pass_args=True))
dp.add_handler(MessageHandler(Filters.private, message))

dp.add_error_handler(error)

t = threading.Thread(target=timer)
t.daemon = True
t.start()

updater.start_polling()
updater.idle()
