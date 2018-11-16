#!/usr/bin/env python2
# -*- coding: utf-8 -*-

# LICENSE: GNU Affero General Public License, version 3
# required packages: python-telegram-bot redis

from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import telegram, logging, time, random, redis, datetime, os, sys
from threading import Thread
import hmac
reload(sys); sys.setdefaultencoding('utf8')

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

bot = None
SG_ID = int(os.environ['SG_ID'])
TOKEN = os.environ['TOKEN']
SECRET = os.environ['SECRET']
global_salt = ''
t_refresh = t_revoke = -1

challenges = open('challenges').readlines()
names = [i.strip().title() for i in open('names').readlines()]

'''
# Challenges created by
for page in range(1,1000):
    html = fetch_url('https://bbs.pku.edu.cn/v2/thread.php?bid=22&mode=single&page=%d'%page)
    postids = re.findall(r'postid=(\d+)"', html)
    users = re.findall(r'name limit">(\w+)<', html)
    assert len(postids) == len(users)
    for t,u in zip(postids, users): print t,u
'''

class STR: pass
STR.challenge_template = '请输入该帖的作者ID（不区分大小写）：\nhttps://bbs.pku.edu.cn/v2/post-read-single.php?bid=22&postid=%s'
STR.welcome = '请 /verify 认证后获得邀请链接'
STR.already_verified = '已认证，如需重新获取邀请链接 /quit 并重新认证'
STR.too_many = '请24小时后再试'
STR.succeeded = '''认证成功，欢迎加入。
接下来向本 bot 发送的消息会被匿名转发到群里，使用前请阅读群内置顶消息。

入群链接（1 分钟内有效）：'''
STR.failed = '回答错误，请 /verify 重试'
STR.quitted = '已退出，如需重新认证请 /verify'
STR.forwarded = '消息已转发，如需删除请回复以下命令：\n/delete %s %s'
STR.deleted = '已删除'
STR.unsupported = '不支持的消息'
STR.whoami = '你当前的标签是 [%s] %s (%s)'
STR.newtag = '''设置成功，有效期一周。如需取消并恢复到自动生成请回复：\n/newtag delete
过期或取消后，如需重新设置成这一标签请回复：\n/newtag %s
注意：除取消外，本命令每天限用一次，重新设置成原来的标签也算一次。'''
STR.tag_default = '自动生成'
STR.tag_ttl = '将于 %d 秒后过期'
STR.tag_reset = '<b>[*] Bot:</b> 匿名标签已经重置'

db = redis.StrictRedis()
now = lambda: time.time()

def refresh_salt():
    global global_salt
    global_salt = os.urandom(16)

def generate_hash(user_id):
    salt = db.get("salt:%s"%user_id) or global_salt
    key = hmac.new(SECRET, salt).digest()
    tag = hmac.new(key, str(user_id)).hexdigest()[:4]
    name = names[int(tag, 16)%len(names)]
    return tag, name

def generate_link():
    link = bot.export_chat_invite_link(SG_ID)
    db.set("invite", link, ex=60)
    print link
    return link

def get_invite_link(bot):
    global t_revoke
    link = db.get("invite") or generate_link()
    t_revoke = now() + 100
    return link

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
    update.message.reply_text(STR.welcome)

def quit(bot, update):
    user_id = update.message.from_user.id
    db.set("auth:%s"%user_id, "QUIT")
    update.message.reply_text(STR.quitted)

def delete(bot, update, args):
    try:
        msg_id, tag = args
        if tag != hmac.new(SECRET, str(msg_id)).hexdigest(): raise
        bot.delete_message(chat_id=SG_ID, message_id=int(msg_id))
        update.message.reply_text(STR.deleted)
    except: pass

def whoami(bot, update):
    user_id = update.message.from_user.id
    ttl = db.ttl("salt:%s"%user_id)
    remark = STR.tag_default if ttl == -2 else STR.tag_ttl%ttl
    tag, name = generate_hash(user_id)
    return update.message.reply_text(STR.whoami%(tag, name, remark))

def newtag(bot, update, args):
    try:
        user_id = update.message.from_user.id
        if db.get("auth:%s"%user_id) != 'OK':
            return start(bot, update)
        salt = args[0] if args else str(random.randint(10000, 99999))
        if salt.lower() == 'delete':
            db.delete("salt:%s"%user_id)
            return update.message.reply_text(STR.deleted)
        if auth_rate_limit(user_id, 'salt', 1):
            return update.message.reply_text(STR.too_many)
        db.set("salt:%s"%user_id, salt, ex=7*86400)
        update.message.reply_text(STR.newtag%salt)
        return whoami(bot, update)
    except: pass

def verify(bot, update):
    user_id = update.message.from_user.id
    auth = db.get("auth:%s"%user_id)
    if auth == 'OK':
        return update.message.reply_text(STR.already_verified)
    if auth_rate_limit(user_id, 'auth', 10):
        return update.message.reply_text(STR.too_many)
    question, answer = get_challenge()
    db.set("answer:%s"%user_id, answer, ex=3600)
    db.set("auth:%s"%user_id, "ANSWER")
    update.message.reply_text(question)

def forward_message(bot, msg):
    """Forward a message."""
    global t_refresh
    if t_refresh is None:
        bot.send_message(SG_ID, STR.tag_reset, parse_mode="HTML")
    if msg.photo:
        r = bot.send_photo(SG_ID, msg.photo[0].file_id, caption=msg.caption)
    elif msg.video:
        r = bot.send_video(SG_ID, msg.video.file_id, caption=msg.caption)
    elif msg.voice:
        r = bot.send_voice(SG_ID, msg.voice.file_id, caption=msg.caption)
    elif msg.document:
        r = bot.send_document(SG_ID, msg.document.file_id, caption=msg.caption)
    else:
        if not msg.text:
            return None
        if msg.text.startswith(('/anon', '/anno')):
            text = '<b>[*] Anonymous:</b> ' + msg.text[5:].strip()
        elif msg.text.startswith('//'):
            text = '<b>[*] Anonymous:</b> ' + msg.text[2:].strip()
        else:
            text = '<b>[%s] %s:</b> '%generate_hash(msg.from_user.id) + msg.text_html
        r = bot.send_message(SG_ID, text, parse_mode="HTML")
    t_refresh = now() + 10000
    return r.message_id

def message(bot, update):
    """Forward the user message, or process verification."""
    msg = update.message
    user_id = msg.from_user.id
    auth = db.get("auth:%s"%user_id)
    if auth == 'OK':
        msg_id = forward_message(bot, msg)
        if msg_id is None:
            return msg.reply_text(STR.unsupported)
        tag = hmac.new(SECRET, str(msg_id)).hexdigest()
        msg.reply_text(STR.forwarded%(msg_id, tag), reply_to_message_id=msg.message_id)
    elif auth == 'ANSWER':
        answer = db.get("answer:%s"%user_id)
        if answer and msg.text.strip().lower() == answer.lower():
            db.set("auth:%s"%user_id, "OK")
            msg.reply_text(STR.succeeded+get_invite_link(bot))
        else:
            db.set("auth:%s"%user_id, "FAIL")
            msg.reply_text(STR.failed)
    else:
        return start(bot, update)

def error(bot, update, error):
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, error)

def timer():
    global t_refresh, t_revoke
    while 1:
        try:
            t = now()
            if t_refresh and t > t_refresh:
                refresh_salt()
                t_refresh = None
            if t_revoke and t > t_revoke:
                generate_link()
                t_revoke = None
        except: pass
        time.sleep(1)

def main():
    """Start the bot."""
    global bot
    updater = Updater(TOKEN)
    bot = updater.bot
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("start", start, Filters.private))
    dp.add_handler(CommandHandler("verify", verify, Filters.private))
    dp.add_handler(CommandHandler("delete", delete, Filters.private, pass_args=True))
    dp.add_handler(CommandHandler("whoami", whoami, Filters.private))
    dp.add_handler(CommandHandler("newtag", newtag, Filters.private, pass_args=True))
    dp.add_handler(CommandHandler("quit", quit, Filters.private))
    dp.add_handler(MessageHandler(Filters.private, message))

    dp.add_error_handler(error)

    t = Thread(target=timer)
    t.daemon = True
    t.start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__': main()
