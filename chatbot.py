#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# LICENSE: GNU Affero General Public License, version 3
# required packages: python-telegram-bot

from telegram import InputMediaVideo, InputMediaPhoto
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import logging, time, random, datetime, os, sys, hmac, hashlib, threading, re

logging.basicConfig(format='%(asctime)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

now = lambda: time.time()
bstr = lambda s: str(s).encode('ascii')
hmac_md5 = lambda key, msg: hmac.new(key, bstr(msg), digestmod=hashlib.md5).hexdigest()

bot = forwarder = None
SG_ID = int(os.environ['SG_ID']) # supergroup ID
CH_ID = int(os.environ['CH_ID']) # channel ID
TOKEN = os.environ['TOKEN']
FWD_TOKEN = os.environ['FWD_TOKEN']
SECRET = bstr(os.environ['SECRET'])

class G:
    salt = ''
    refresh = revoke = 1

names_in_use = {}
banned_names = {}
admin_status = ("creator", "administrator")
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
STR.banned = '%s 已被暂时禁言'
STR.whoami = '你当前的标签是 [%s]'
STR.tag_reset = '匿名标签已重置'
STR.poll_help = '请直接在 bot 私聊中发起投票'

class DB:
    def __init__(self):
        self.dict = dict()
        self.ttl = dict()
    def incr(self, key):
        r = int(self.get(key) or 0) + 1
        self.dict[key] = r
        return r
    def pop(self, key):
        self.ttl.pop(key, None)
        return self.dict.pop(key, None)
    def expire(self, key, ex):
        self.ttl[key] = ex+now() if ex else None
    def get(self, key):
        if key not in self.dict:
            return None
        if key in self.ttl and self.ttl[key] < now():
            self.pop(key)
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
    return name or 'Unnamed'

def generate_link():
    link = str(bot.export_chat_invite_link(SG_ID))
    db.set("invite", link, ex=60)
    print(link)
    return link

def get_invite_link():
    link = db.get("invite") or generate_link()
    G.revoke = now() + 100
    return link

def get_member_status(user_id):
    try:
        member = bot.get_chat_member(SG_ID, user_id)
        return member.status
    except: return None

def user_in_group(user_id, use_cache=True):
    k = "auth:%s"%user_id
    if use_cache and db.get(k):
        return db.get(k) > 0
    r = +1 if get_member_status(user_id) in allowed_status else -1
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

def announce(msg):
    r = bot.send_message(SG_ID, '<b>[Bot] </b>'+msg, parse_mode="HTML")
    return r.message_id

def ping(update, context):
    update.message.reply_text('pong')

def start(update, context):
    user_id = update.message.from_user.id
    text = STR.verified if user_in_group(user_id) else STR.welcome
    update.message.reply_text(text)

def delete(update, context):
    try:
        msg_id, key = context.args
        if key != hmac_md5(SECRET, msg_id): raise
        for i in msg_id.split(','):
            chmsg_id = int(i) >> 24
            sgmsg_id = int(i) & 0xffffff
            if chmsg_id:
                forwarder.delete_message(chat_id=SG_ID, message_id=sgmsg_id)
                bot.delete_message(chat_id=CH_ID, message_id=chmsg_id)
            else:
                bot.delete_message(chat_id=SG_ID, message_id=sgmsg_id)
        update.message.reply_text(STR.deleted)
    except: pass

def whoami(update, context):
    user_id = update.message.from_user.id
    if not user_in_group(user_id):
        return start(update, context)
    name = generate_hash(user_id)
    return update.message.reply_text(STR.whoami%name)

def verify(update, context):
    user_id = update.message.from_user.id
    if user_in_group(user_id, use_cache=False):
        return update.message.reply_text(STR.verified)
    if auth_rate_limit(user_id, 'auth', 10):
        return update.message.reply_text(STR.too_many)
    question, answer = get_challenge()
    db.set("answer:%s"%user_id, answer, ex=3600)
    update.message.reply_text(question)

def ban(update, context):
    args = context.args
    user_id = update.message.from_user.id
    if get_member_status(user_id) not in admin_status: return
    name = str(args[0]).title()
    if name.startswith('-'): # unban
        if banned_names.pop(name.strip('-'), None):
            update.message.reply_text('OK')
    elif name in names or name == 'Anonymous':
        till = (now() + int(args[1])) if len(args)>1 else 0
        banned_names[name] = till
        announce(STR.banned%name)
    else:
        update.message.reply_text(str())

def extract_media(msg, txt=None):
    caption = txt or msg.caption_html
    if msg.photo:
        return InputMediaPhoto(msg.photo[-1].file_id, caption=caption, parse_mode="HTML")
    elif msg.video:
        return InputMediaVideo(msg.video.file_id, caption=caption, parse_mode="HTML")

def send_media_group(msg, media):
    r = bot.send_media_group(SG_ID, media)
    msg_id = ','.join([str(i.message_id) for i in r])
    text = STR.forwarded%(msg_id, hmac_md5(SECRET, msg_id))
    msg.reply_text(text, reply_to_message_id=msg.message_id)

def forward_message(msg, queue):
    if G.refresh is None:
        announce(STR.tag_reset)
        G.refresh = 0 # suppress duplicate announcements

    name = generate_hash(msg.from_user.id)
    raw_txt = msg.text or msg.caption or ''
    txt = msg.text_html or msg.caption_html or ''
    if msg.poll: txt = raw_txt = msg.poll.question
    if re.match(r'/poll\b', raw_txt):
        return msg.reply_text(STR.poll_help)

    mode = "HTML"
    if raw_txt.startswith('//'):
        raw_txt = raw_txt[2:].strip()
        txt = re.sub(r'(?<!<)\/', '', txt, 2)
        name = 'Anonymous'

    raw_txt = '[%s] '%name + raw_txt
    txt = '<b>[%s] </b>'%name + txt
    chmsg_id = 0

    if name in banned_names:
        return msg.reply_text(STR.banned%name)

    media = []
    if msg.media_group_id:
        media.append(extract_media(msg, txt))
        deque = queue.queue
        while deque and msg.media_group_id == deque[0].message.media_group_id:
            next = queue.get_nowait().message
            media.append(extract_media(next))
    if len(media) > 1:
        return send_media_group(msg, media)

    if msg.photo:
        r = bot.send_photo(SG_ID, msg.photo[-1].file_id, caption=txt, parse_mode=mode)
    elif msg.video:
        r = bot.send_video(SG_ID, msg.video.file_id, caption=txt, parse_mode=mode)
    elif msg.document:
        r = bot.send_document(SG_ID, msg.document.file_id, caption=txt, parse_mode=mode)
    elif msg.poll:
        r = bot.send_poll(SG_ID, raw_txt, [i.text for i in msg.poll.options])
    elif raw_txt:
        if any([i.type in ('url', 'text_link') for i in msg.entities]):
            chmsg = bot.send_message(CH_ID, txt, parse_mode=mode)
            chmsg_id = chmsg.message_id
            r = forwarder.forward_message(SG_ID, CH_ID, chmsg_id)
        else:
            r = bot.send_message(SG_ID, txt, parse_mode=mode)
    else:
        return msg.reply_text(STR.unsupported)

    G.refresh = now() + 10000
    msg_id = (chmsg_id << 24) + r['message_id']
    text = STR.forwarded%(msg_id, hmac_md5(SECRET, msg_id))
    return msg.reply_text(text, reply_to_message_id=msg.message_id)

def del_message(update, context):
    update.message.delete()

def message(update, context):
    msg = update.message
    user_id = msg.from_user.id

    if user_in_group(user_id):
        return forward_message(msg, context.update_queue)

    answer = db.pop("answer:%s"%user_id)
    if answer is None:
        return start(update, context)

    if msg.text.strip().lower() == answer.lower():
        db.set("auth:%s"%user_id, 1, ex=100)
        msg.reply_text(STR.succeeded%get_invite_link())
    else:
        msg.reply_text(STR.failed)

def error(update, context):
    logger.warning('Update "%s" caused error "%s"', update, context.error)

def timer():
    while 1:
        try:
            t = now()
            refresh_time = max(0, G.refresh or 0, *banned_names.values())
            if refresh_time and t > refresh_time:
                G.salt = os.urandom(16)
                names_in_use.clear()
                banned_names.clear()
                G.refresh = None
            if G.revoke and t > G.revoke:
                generate_link()
                G.revoke = None
        except: pass
        time.sleep(1)

forwarder = Updater(FWD_TOKEN, use_context=True).bot
updater = Updater(TOKEN, use_context=True)
bot = updater.bot
dp = updater.dispatcher

dp.add_handler(CommandHandler("ping", ping))
dp.add_handler(CommandHandler("ban", ban))
dp.add_handler(CommandHandler("help", start, Filters.private))
dp.add_handler(CommandHandler("start", start, Filters.private))
dp.add_handler(CommandHandler("verify", verify, Filters.private))
dp.add_handler(CommandHandler("whoami", whoami, Filters.private))
dp.add_handler(CommandHandler("delete", delete, Filters.private))
dp.add_handler(MessageHandler(Filters.private, message))
dp.add_handler(MessageHandler(Filters.status_update.new_chat_members, del_message))
dp.add_handler(MessageHandler(Filters.status_update.left_chat_member, del_message))

dp.add_error_handler(error)

t = threading.Thread(target=timer)
t.daemon = True
t.start()

updater.start_polling()
updater.idle()
